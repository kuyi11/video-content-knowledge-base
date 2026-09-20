import argparse
import hashlib
import json
import logging
import os
import sys
import torch
from pathlib import Path

from config import (
    ASR_BACKEND,
    BGE_MODEL_PATH,
    INDEX_DIR,
    SUMMARY_PROFILES,
    TEMP_DIR,
    VAULT_DIR,
    EXTERNAL_VAULT_PATHS,
    WHISPER_HOTWORDS_ENABLED,
)
from modules.embedding_runtime import SentenceTransformer
from modules.audio import get_audio
from modules.transcribe import get_cached_asr_transcript, transcribe
from modules.asr.glossary import (
    build_backend_hotwords,
    load_domain_entries,
    normalize_domains,
)
from modules.asr.types import normalize_backend_name
from modules.subtitle import (
    TranscriptArtifact,
    get_embedded_transcript,
    get_cached_subtitle_transcript,
    get_platform_transcript,
    load_transcript_metadata,
)
from modules.content_map import extract_content_map
from modules.structure import render_markdown, structure_content_map
from modules.obsidian_writer import write_to_obsidian
from modules.vault_loader import VaultLoader
from modules.indexer import HybridIndex
from modules.document_identity import extract_video_id, make_document_id
from modules.file_lock import FileLock
from modules.prompt_profiles import (
    DEFAULT_PROFILE_NAME,
    DEFAULT_SCHEMA_VERSION,
    PromptProfile,
    available_profiles,
    load_prompt_profile,
)

logger = logging.getLogger(__name__)
STATE_VERSION = 2


class Pipeline:
    def __init__(self, vault_path=VAULT_DIR, index_dir=INDEX_DIR, default_profile=None, external_vault_paths=None):
        self.vault_path = Path(vault_path)
        self.vault_path.mkdir(parents=True, exist_ok=True)
        self.index_dir = Path(index_dir)
        self.index_dir.mkdir(parents=True, exist_ok=True)
        self.default_profiles = tuple(SUMMARY_PROFILES if default_profile is None else [default_profile])
        self.default_profile = self.default_profiles[0]
        self.external_vault_paths = tuple(
            Path(path) for path in (EXTERNAL_VAULT_PATHS if external_vault_paths is None else external_vault_paths)
            if Path(path).exists() and Path(path).resolve() != self.vault_path.resolve()
        )
        self._model = None

    def _load_vault_documents(self, model):
        paths = (self.vault_path, *self.external_vault_paths)
        return VaultLoader(paths, model, temp_dir=TEMP_DIR).load_all()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        if self._model is not None:
            del self._model
            self._model = None
        if torch.cuda.is_available():
            torch.cuda.empty_cache()

    def _get_model(self):
        if self._model is None:
            device = os.environ.get("EMBEDDING_DEVICE")
            if not device:
                device = "cuda" if torch.cuda.is_available() else "cpu"
            self._model = SentenceTransformer(BGE_MODEL_PATH, device=device)
        return self._model

    def _get_profile(self, profile: str | PromptProfile | None) -> PromptProfile:
        if isinstance(profile, PromptProfile):
            return profile
        return load_prompt_profile(profile or self.default_profile)

    def _get_profiles(
        self,
        *,
        profile: str | PromptProfile | None = None,
        profiles: str | list[str | PromptProfile] | tuple[str | PromptProfile, ...] | None = None,
    ) -> list[PromptProfile]:
        if profile is not None and profiles is not None:
            raise ValueError("Use either profile or profiles, not both")

        if profiles is None:
            raw_profiles = [profile] if profile is not None else list(self.default_profiles)
        elif isinstance(profiles, str):
            raw_profiles = [name.strip() for name in profiles.split(",") if name.strip()]
        else:
            raw_profiles = list(profiles)
        if not raw_profiles:
            raise ValueError("At least one summary profile is required")

        resolved = []
        seen = set()
        for raw_profile in raw_profiles:
            prompt_profile = self._get_profile(raw_profile)
            if prompt_profile.name not in seen:
                resolved.append(prompt_profile)
                seen.add(prompt_profile.name)
        return resolved

    def run(
        self,
        url: str,
        force_reindex: bool = False,
        profile: str | PromptProfile | None = None,
        profiles: str | list[str | PromptProfile] | tuple[str | PromptProfile, ...] | None = None,
        media_path: str | Path | None = None,
        asr_backend: str | None = None,
        domains: str | list[str] | tuple[str, ...] | None = None,
    ) -> dict:
        bv = self._extract_bv(url)
        if not bv:
            return {"status": "error", "step": 0, "error": f"Cannot extract video ID: {url}"}

        try:
            prompt_profiles = self._get_profiles(profile=profile, profiles=profiles)
        except Exception as e:
            return {"status": "error", "step": 0, "error": f"Invalid prompt profile: {e}"}

        selected_backend = normalize_backend_name(asr_backend or ASR_BACKEND)
        if selected_backend not in {"faster_whisper", "paraformer"}:
            return {"status": "error", "step": 0, "error": f"Invalid ASR backend: {selected_backend}"}
        try:
            selected_domains = normalize_domains(domains)
            hotword_entries = load_domain_entries(selected_domains)
            hotwords = build_backend_hotwords(hotword_entries, selected_backend)
            if selected_backend == "faster_whisper" and not WHISPER_HOTWORDS_ENABLED:
                hotwords = ""
            requested_asr = self._asr_state_config(selected_backend, selected_domains, hotwords)
        except Exception as e:
            return {"status": "error", "step": 0, "error": f"Invalid ASR domains: {e}"}
        profile_results = {}
        pending_profiles = []
        for prompt_profile in prompt_profiles:
            if not force_reindex and self._already_processed(bv, prompt_profile, asr_config=requested_asr):
                profile_results[prompt_profile.name] = {
                    "status": "skipped",
                    "profile": prompt_profile.name,
                }
            else:
                pending_profiles.append(prompt_profile)

        if not pending_profiles:
            return self._format_run_result(bv, prompt_profiles, profile_results)

        step = 0
        try:
            step = 1
            logger.info("[Step 1] Resolving transcript source: %s", url)
            artifact = None if force_reindex else get_cached_subtitle_transcript(bv)
            cached_asr = (
                None
                if force_reindex or artifact
                else get_cached_asr_transcript(
                    bv,
                    asr_backend=selected_backend,
                    hotwords=hotwords,
                    domains=selected_domains,
                )
            )
            if cached_asr:
                transcript_metadata = load_transcript_metadata(cached_asr)
                artifact = TranscriptArtifact(
                    path=Path(cached_asr),
                    text_source="asr",
                    language=transcript_metadata.get("language_detected"),
                    provider=str(transcript_metadata.get("asr_backend", selected_backend)),
                    fallback_chain=tuple(transcript_metadata.get("fallback_chain", [])),
                )
                transcript_metadata.update(artifact.metadata())
                logger.info("[Step 2] Reusing cached ASR transcript: %s", cached_asr)

            if artifact is None:
                artifact = get_platform_transcript(url, bv, force=force_reindex)
            if artifact is None:
                artifact = get_embedded_transcript(
                    media_path,
                    bv,
                    force=force_reindex,
                )

            audio_path = None
            if artifact is None:
                logger.info("[Step 1] No usable subtitle, downloading audio")
                audio_path = get_audio(url)
                logger.info("[Step 1] Audio: %s", audio_path)
                step = 2
                logger.info("[Step 2] Transcribing with ASR...")
                transcript_path = transcribe(
                    str(audio_path),
                    force=force_reindex,
                    output_path=TEMP_DIR / f"{bv}_norm.txt",
                    asr_backend=selected_backend,
                    hotwords=hotwords,
                    domains=selected_domains,
                )
                transcript_metadata = load_transcript_metadata(transcript_path)
                artifact = TranscriptArtifact(
                    path=Path(transcript_path),
                    text_source="asr",
                    language=transcript_metadata.get("language_detected"),
                    provider=str(transcript_metadata.get("asr_backend", selected_backend)),
                    fallback_chain=tuple(transcript_metadata.get("fallback_chain", [])),
                )
                transcript_metadata.update(artifact.metadata())
            else:
                transcript_path = str(artifact.path)
                logger.info(
                    "[Step 2] Subtitle transcript selected: source=%s language=%s path=%s",
                    artifact.text_source,
                    artifact.language or "unknown",
                    artifact.path,
                )
                if artifact.text_source != "asr":
                    transcript_metadata = artifact.metadata()

            transcript_text = Path(transcript_path).read_text(encoding="utf-8")
            logger.info("[Step 2] Transcript: %s", transcript_path)

            step = 3
            logger.info("[Step 3] Structuring...")
            pure_text = "\n".join(
                line for line in transcript_text.splitlines()
                if line and not line.startswith("#")
            )
            content_map = extract_content_map(
                pure_text,
                source_id=bv,
                domains=selected_domains,
                force=force_reindex,
            )

            written_profiles: list[tuple[PromptProfile, str]] = []
            for prompt_profile in pending_profiles:
                try:
                    step = 3
                    logger.info("[Step 3] Synthesizing profile=%s...", prompt_profile.name)
                    structured = structure_content_map(
                        content_map,
                        profile=prompt_profile,
                        source_id=bv,
                    )

                    step = 4
                    logger.info("[Step 4] Writing profile=%s to vault...", prompt_profile.name)
                    md = render_markdown(
                        structured,
                        url,
                        profile=prompt_profile.name,
                        schema_version=prompt_profile.schema_version,
                        prompt_version=prompt_profile.prompt_version,
                        content_map=content_map,
                        transcript_metadata=transcript_metadata,
                    )
                    md_path = write_to_obsidian(
                        md,
                        url,
                        output_dir=self.vault_path,
                        overwrite=True,
                        profile=prompt_profile.name,
                    )
                    written_profiles.append((prompt_profile, md_path))
                    profile_results[prompt_profile.name] = {
                        "status": "ok",
                        "profile": prompt_profile.name,
                        "schema_version": prompt_profile.schema_version,
                        "title": structured.get("title", bv),
                        "md_path": md_path,
                    }
                except Exception as exc:
                    logger.exception("Profile synthesis failed: %s", prompt_profile.name)
                    profile_results[prompt_profile.name] = {
                        "status": "error",
                        "step": step,
                        "profile": prompt_profile.name,
                        "error": str(exc),
                    }

            if written_profiles:
                step = 5
                logger.info("[Step 5] Updating index for %d profile(s)...", len(written_profiles))
                try:
                    chunk_count = self._update_index(
                        bv,
                        [item[0] for item in written_profiles],
                        force_reindex=force_reindex,
                    )
                    for prompt_profile, md_path in written_profiles:
                        profile_results[prompt_profile.name]["chunks_indexed"] = chunk_count
                        self._mark_completed(
                            bv,
                            md_path,
                            prompt_profile,
                            transcript_state=self._transcript_state(
                                artifact, transcript_metadata, requested_asr
                            ),
                        )
                except Exception as exc:
                    logger.exception("Index update failed")
                    for prompt_profile, _ in written_profiles:
                        profile_results[prompt_profile.name] = {
                            "status": "error",
                            "step": 5,
                            "profile": prompt_profile.name,
                            "error": str(exc),
                        }

            common = {
                "audio_path": str(audio_path) if audio_path else None,
                "transcript_path": transcript_path,
                "text_source": artifact.text_source,
                "source_language": artifact.language,
                "fallback_chain": list(artifact.fallback_chain),
                "asr_backend": transcript_metadata.get("asr_backend") if artifact.text_source == "asr" else None,
                "domains": list(selected_domains),
            }
            return self._format_run_result(bv, prompt_profiles, profile_results, common=common)

        except Exception as e:
            import traceback
            traceback.print_exc()
            return {"status": "error", "step": step, "error": str(e)}
        finally:
            if torch.cuda.is_available():
                torch.cuda.empty_cache()

    def _update_index(
        self,
        video_id: str,
        profiles: list[PromptProfile],
        *,
        force_reindex: bool,
    ) -> int:
        model = self._get_model()
        index = HybridIndex(self.index_dir, model=model)
        all_documents = self._load_vault_documents(model)
        if force_reindex or not (self.index_dir / "vector" / "faiss.index").exists():
            if not all_documents:
                raise RuntimeError("No documents in vault")
            index.build(all_documents, force=True)
        elif index.faiss_index is None:
            index.build(all_documents, force=True)
        else:
            health = index.health_check(all_documents)
            if health["status"] != "ok":
                logger.info(
                    "[Step 5] Vault/index mismatch: missing=%d extra=%d changed=%d; rebuilding",
                    len(health["missing_from_index"]),
                    len(health["extra_in_index"]),
                    len(health["changed_sources"]),
                )
                index.build(all_documents, force=True)
                return len(index.chunks)
            documents = [
                document
                for prompt_profile in profiles
                if (document := VaultLoader((self.vault_path, *self.external_vault_paths), model, temp_dir=TEMP_DIR).load_one(video_id, prompt_profile.name))
            ]
            if documents:
                index.add_documents(documents)
                index.write_health_report(all_documents)
            else:
                docs = VaultLoader(self.vault_path, model, temp_dir=TEMP_DIR).load_all()
                index.build(docs, force=True)
        chunk_count = len(index.chunks)
        logger.info("[Step 5] Indexed: %d chunks", chunk_count)
        return chunk_count

    @staticmethod
    def _format_run_result(
        video_id: str,
        selected_profiles: list[PromptProfile],
        profile_results: dict,
        *,
        common: dict | None = None,
    ) -> dict:
        common = common or {}
        statuses = [profile_results[profile.name]["status"] for profile in selected_profiles]
        if all(status == "skipped" for status in statuses):
            status = "skipped"
        elif all(status == "error" for status in statuses):
            status = "error"
        elif any(status == "error" for status in statuses):
            status = "partial"
        else:
            status = "ok"

        if len(selected_profiles) == 1:
            profile_result = dict(profile_results[selected_profiles[0].name])
            return {"video_id": video_id, **common, **profile_result}
        return {
            "status": status,
            "video_id": video_id,
            **common,
            "profiles": profile_results,
        }

    def _extract_bv(self, url: str) -> str | None:
        return extract_video_id(url)

    @staticmethod
    def _empty_state() -> dict:
        return {"version": STATE_VERSION, "videos": {}}

    def _normalize_state(self, state: dict) -> dict:
        if state.get("version") == STATE_VERSION and isinstance(state.get("videos"), dict):
            return state

        migrated = self._empty_state()
        for video_id, video_state in state.items():
            if not isinstance(video_state, dict) or "status" not in video_state:
                continue
            profile_name = str(video_state.get("profile", DEFAULT_PROFILE_NAME))
            migrated["videos"].setdefault(video_id, {})[profile_name] = video_state
        return migrated

    def _load_state(self) -> dict:
        state_path = self.index_dir / "pipeline_state.json"
        if state_path.exists():
            try:
                raw_state = json.loads(state_path.read_text(encoding="utf-8"))
                if isinstance(raw_state, dict):
                    return self._normalize_state(raw_state)
                logger.warning("Invalid pipeline_state.json root, starting fresh")
                return self._empty_state()
            except json.JSONDecodeError:
                logger.warning("Corrupted pipeline_state.json, starting fresh")
                return self._empty_state()
        return self._empty_state()

    def _save_state(self, state: dict):
        state_path = self.index_dir / "pipeline_state.json"
        tmp = state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(str(tmp), str(state_path))

    @staticmethod
    def _asr_state_config(backend: str, domains, hotwords: str | list[str] | None) -> dict:
        payload = hotwords if isinstance(hotwords, str) else list(hotwords or [])
        digest = (
            hashlib.sha256(
                json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
            ).hexdigest()
            if payload
            else "none"
        )
        return {
            "asr_backend": normalize_backend_name(backend),
            "domains": list(normalize_domains(domains)),
            "hotword_sha256": digest,
        }

    @staticmethod
    def _transcript_state(
        artifact: TranscriptArtifact,
        metadata: dict,
        requested_asr: dict,
    ) -> dict:
        state = {"text_source": artifact.text_source}
        if artifact.text_source == "asr":
            state.update(
                {
                    "asr_backend": metadata.get(
                        "asr_backend", requested_asr["asr_backend"]
                    ),
                    "domains": metadata.get("domains", requested_asr["domains"]),
                    "hotword_sha256": metadata.get(
                        "hotword_sha256", requested_asr["hotword_sha256"]
                    ),
                }
            )
        return state

    def _mark_completed(
        self,
        video_id: str,
        md_path: str,
        profile: PromptProfile | None = None,
        *,
        transcript_state: dict | None = None,
    ):
        profile = profile or load_prompt_profile(self.default_profile)
        with FileLock(self.index_dir / ".pipeline_state.lock"):
            state = self._load_state()
            state["videos"].setdefault(video_id, {})[profile.name] = {
                "status": "completed",
                "md_path": md_path,
                "profile": profile.name,
                "schema_version": profile.schema_version,
                "prompt_version": profile.prompt_version,
                "transcript": dict(transcript_state or {}),
                "steps_completed": [1, 2, 3, 4, 5],
            }
            self._save_state(state)

    def _state_matches_profile(self, video_state: dict, profile: PromptProfile) -> bool:
        state_profile = video_state.get("profile", DEFAULT_PROFILE_NAME)
        state_schema_version = video_state.get("schema_version", DEFAULT_SCHEMA_VERSION)
        state_prompt_version = video_state.get("prompt_version")
        if state_profile != profile.name:
            return False
        if state_schema_version != profile.schema_version:
            return False
        if state_prompt_version != profile.prompt_version:
            return False
        return True

    @staticmethod
    def _state_matches_transcript(video_state: dict, asr_config: dict) -> bool:
        transcript = video_state.get("transcript", {})
        text_source = transcript.get("text_source")
        if not text_source:
            return False
        if text_source != "asr":
            return True
        return all(transcript.get(key) == value for key, value in asr_config.items())

    def _already_processed(
        self,
        video_id: str,
        profile: PromptProfile | None = None,
        *,
        asr_config: dict | None = None,
    ) -> bool:
        profile = profile or load_prompt_profile(self.default_profile)
        state = self._load_state()
        video_state = state.get("videos", {}).get(video_id, {}).get(profile.name, {})
        if (
            video_state.get("status") == "completed"
            and self._state_matches_profile(video_state, profile)
            and (
                asr_config is None
                or self._state_matches_transcript(video_state, asr_config)
            )
        ):
            md_path = video_state.get("md_path", "")
            if md_path and Path(md_path).exists():
                faiss_exists = (self.index_dir / "vector" / "faiss.index").exists()
                whoosh_exists = (self.index_dir / "keyword" / "whoosh").exists()
                vector_manifest = self.index_dir / "vector" / "manifest.json"
                keyword_manifest = self.index_dir / "keyword" / "whoosh" / "manifest.json"
                chunks_path = self.index_dir / "vector" / "chunks.json"
                manifests_match = False
                if vector_manifest.exists() and keyword_manifest.exists():
                    try:
                        vector_meta = json.loads(vector_manifest.read_text(encoding="utf-8"))
                        keyword_meta = json.loads(keyword_manifest.read_text(encoding="utf-8"))
                        manifests_match = (
                            vector_meta.get("generation_id") == keyword_meta.get("generation_id")
                            and vector_meta.get("chunk_count") == keyword_meta.get("chunk_count")
                        )
                    except (OSError, json.JSONDecodeError):
                        manifests_match = False
                contains_document = False
                document_id = make_document_id(video_id, profile.name)
                if chunks_path.exists():
                    try:
                        chunks = json.loads(chunks_path.read_text(encoding="utf-8"))
                        contains_document = any(
                            str(c.get("document_id", "")) == document_id
                            for c in chunks
                        )
                    except (OSError, json.JSONDecodeError, TypeError, AttributeError):
                        contains_document = False
                if not (faiss_exists and whoosh_exists and manifests_match and contains_document):
                    logger.warning(
                        "[%s/%s] state says completed but index incomplete, reprocessing",
                        video_id,
                        profile.name,
                    )
                    return False
                return True
        return False


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="VideoContentKnowledgeBase pipeline")
    parser.add_argument(
        "--force-reindex",
        action="store_true",
        help="reprocess the video and rebuild/update the index",
    )
    parser.add_argument(
        "--profile",
        default=None,
        help="single profile compatibility option; prefer --profiles",
    )
    parser.add_argument(
        "--profiles",
        default=None,
        help=(
            "comma-separated summary profiles. "
            f"Available: {', '.join(available_profiles())}. "
            f"Default: {', '.join(SUMMARY_PROFILES)}"
        ),
    )
    parser.add_argument(
        "--asr-backend",
        choices=("faster-whisper", "paraformer"),
        default=None,
        help=f"ASR backend used when subtitles are unavailable. Default: {ASR_BACKEND}",
    )
    parser.add_argument(
        "--domains",
        default=None,
        help="comma-separated ASR terminology domains, for example fitness,nutrition,medical",
    )
    parser.add_argument(
        "--media-path",
        default=None,
        help="optional existing local media file to inspect for embedded subtitle tracks",
    )
    parser.add_argument("urls", nargs="+", help="Bilibili video URL(s)")
    args = parser.parse_args()

    try:
        if args.profile and args.profiles:
            raise ValueError("Use either --profile or --profiles, not both")
        selected_names = (
            [name.strip() for name in args.profiles.split(",") if name.strip()]
            if args.profiles
            else [args.profile] if args.profile
            else list(SUMMARY_PROFILES)
        )
        prompt_profiles = [load_prompt_profile(name) for name in selected_names]
    except Exception as e:
        print(f"Invalid profile selection: {e}")
        sys.exit(1)

    with Pipeline() as p:
        if args.media_path and len(args.urls) != 1:
            parser.error("--media-path requires exactly one URL")
        for url in args.urls:
            result = p.run(
                url,
                force_reindex=args.force_reindex,
                profiles=prompt_profiles,
                media_path=args.media_path,
                asr_backend=args.asr_backend,
                domains=args.domains,
            )
            print("\n" + "=" * 50)
            for k, v in result.items():
                print(f"  {k}: {v}")
