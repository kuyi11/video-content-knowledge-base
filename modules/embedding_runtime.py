"""Runtime loading for text embeddings.

Some Windows environments leave a partial torchvision namespace behind while
installing CUDA packages. Sentence Transformers does not need torchvision for
text embeddings, so mark that optional backend unavailable when its IO module
is absent instead of letting transformers fail during import.
"""

import importlib.util


def _disable_broken_torchvision() -> None:
    if importlib.util.find_spec("torchvision") is None:
        return
    if importlib.util.find_spec("torchvision.io") is not None:
        return

    import transformers.utils as transformers_utils
    import transformers.utils.import_utils as import_utils

    unavailable = lambda: False
    import_utils.is_torchvision_available = unavailable
    transformers_utils.is_torchvision_available = unavailable


_disable_broken_torchvision()

from sentence_transformers import SentenceTransformer

__all__ = ["SentenceTransformer"]
