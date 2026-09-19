from modules.asr.glossary import (
    build_backend_hotwords,
    load_domain_entries,
    normalize_domains,
)


def test_domain_glossaries_deduplicate_terms_and_never_promote_aliases():
    entries = load_domain_entries("fitness,nutrition")
    canonicals = [entry["canonical"] for entry in entries]

    assert normalize_domains(" Fitness, nutrition,fitness ") == ("fitness", "nutrition")
    assert canonicals.count("肌糖原") == 1
    assert all("鸡糖原" not in entry["canonical"] for entry in entries)
    assert "肌糖原" in build_backend_hotwords(entries, "paraformer")
    assert " 20" not in build_backend_hotwords(entries, "paraformer")
    paraformer_hotwords = build_backend_hotwords(entries, "paraformer")
    for alias in ("鸡糖原", "鸡糖圆", "积碳圆", "肌汤缘"):
        assert alias not in paraformer_hotwords
    assert "肌糖原" in build_backend_hotwords(entries, "faster-whisper")