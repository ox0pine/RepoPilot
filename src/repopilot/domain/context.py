from __future__ import annotations

from pydantic import BaseModel, ConfigDict, StrictBool, StrictStr


class ContextFile(BaseModel):
    model_config = ConfigDict(extra='forbid')

    path: StrictStr
    blob_sha: StrictStr
    content: StrictStr
    truncated: StrictBool
    reason: StrictStr


class RepositoryContext(BaseModel):
    model_config = ConfigDict(extra='forbid')

    commit: StrictStr
    tree: list[StrictStr]
    files: list[ContextFile]
    omissions: list[StrictStr]
    tree_truncated: StrictBool
