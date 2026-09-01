"""A minimal in-memory stand-in for the pymongo collection API.

Covers only the operations this application actually performs. It is
deliberately partial — a fake that is obviously incomplete is safer than one
that looks complete and diverges under conditions nobody tested.

Shared by the load-test server and the cache/analytics checks so both exercise
the same behaviour.
"""
from __future__ import annotations

import copy
import itertools
from typing import Any


def _matches(doc: dict, query: dict) -> bool:
    """Support the handful of query forms used in this codebase."""
    for field, condition in query.items():
        value = doc.get(field)
        if isinstance(condition, dict):
            for op, operand in condition.items():
                if op == "$nin" and value in operand:
                    return False
                if op == "$in" and value not in operand:
                    return False
                if op == "$gte" and not (value is not None and value >= operand):
                    return False
                if op == "$regex":
                    import re as _re
                    flags = _re.IGNORECASE if "i" in condition.get("$options", "") else 0
                    if value is None or not _re.search(operand, str(value), flags):
                        return False
        elif value != condition:
            return False
    return True


def _project(doc: dict, projection: dict | None) -> dict:
    if not projection:
        return copy.deepcopy(doc)
    excludes = {k for k, v in projection.items() if v == 0}
    includes = {k for k, v in projection.items() if v == 1}
    out = {}
    for key, value in doc.items():
        if key in excludes:
            continue
        if includes and key not in includes and key != "_id":
            continue
        out[key] = copy.deepcopy(value)
    if "_id" in excludes:
        out.pop("_id", None)
    return out


def _set_path(doc: dict, path: str, value: Any) -> None:
    """Assign through a dotted path, so "messages.3.flag" reaches into the
    list the way Mongo's positional update does."""
    parts = path.split(".")
    target: Any = doc
    for part in parts[:-1]:
        target = target[int(part)] if isinstance(target, list) else target.setdefault(part, {})
    last = parts[-1]
    if isinstance(target, list):
        target[int(last)] = value
    else:
        target[last] = value


def _apply_update(doc: dict, update: dict, inserted: bool) -> None:
    for field, value in update.get("$set", {}).items():
        _set_path(doc, field, value)
    for field, value in update.get("$inc", {}).items():
        doc[field] = doc.get(field, 0) + value
    for field, spec in update.get("$push", {}).items():
        doc.setdefault(field, []).extend(spec["$each"] if "$each" in spec else [spec])
    if inserted:
        for field, value in update.get("$setOnInsert", {}).items():
            doc.setdefault(field, value)


class _Cursor:
    def __init__(self, docs: list[dict]):
        self._docs = docs

    def sort(self, key, direction=1):
        if isinstance(key, list):
            for field, dirn in reversed(key):
                self._docs.sort(key=lambda d: (d.get(field) is None, d.get(field)),
                                reverse=dirn < 0)
        else:
            self._docs.sort(key=lambda d: (d.get(key) is None, d.get(key)),
                            reverse=direction < 0)
        return self

    def skip(self, n):
        self._docs = self._docs[n:]
        return self

    def limit(self, n):
        self._docs = self._docs[:n]
        return self

    def __iter__(self):
        return iter(self._docs)


class FakeCollection:
    def __init__(self, name: str = ""):
        self.name = name
        self._docs: list[dict] = []
        self._ids = itertools.count(1)

    # ── reads ────────────────────────────────────────────────────────────────
    def find_one(self, query: dict, projection: dict | None = None):
        for doc in self._docs:
            if _matches(doc, query):
                return _project(doc, projection)
        return None

    def find(self, query: dict | None = None, projection: dict | None = None):
        return _Cursor([_project(d, projection)
                        for d in self._docs if _matches(d, query or {})])

    def count_documents(self, query: dict) -> int:
        return sum(1 for d in self._docs if _matches(d, query))

    def distinct(self, field: str):
        return list({d.get(field) for d in self._docs})

    # ── writes ───────────────────────────────────────────────────────────────
    def insert_one(self, doc: dict):
        doc = copy.deepcopy(doc)
        doc.setdefault("_id", next(self._ids))
        self._docs.append(doc)
        return type("R", (), {"inserted_id": doc["_id"]})()

    def insert_many(self, docs: list[dict]):
        for d in docs:
            self.insert_one(d)

    def update_one(self, query: dict, update: dict, upsert: bool = False):
        for doc in self._docs:
            if _matches(doc, query):
                _apply_update(doc, update, inserted=False)
                return type("R", (), {"matched_count": 1, "modified_count": 1})()
        if upsert:
            doc = {k: v for k, v in query.items() if not isinstance(v, dict)}
            doc.setdefault("_id", next(self._ids))
            _apply_update(doc, update, inserted=True)
            self._docs.append(doc)
            return type("R", (), {"matched_count": 0, "modified_count": 0})()
        return type("R", (), {"matched_count": 0, "modified_count": 0})()

    def find_one_and_update(self, query: dict, update: dict, upsert: bool = False,
                            return_document: Any = True, **kwargs):
        for doc in self._docs:
            if _matches(doc, query):
                _apply_update(doc, update, inserted=False)
                return copy.deepcopy(doc)
        if upsert:
            doc = {k: v for k, v in query.items() if not isinstance(v, dict)}
            doc.setdefault("_id", next(self._ids))
            _apply_update(doc, update, inserted=True)
            self._docs.append(doc)
            return copy.deepcopy(doc)
        return None

    def update_many(self, query: dict, update: dict):
        for doc in self._docs:
            if _matches(doc, query):
                _apply_update(doc, update, inserted=False)

    def delete_one(self, query: dict):
        for i, doc in enumerate(self._docs):
            if _matches(doc, query):
                del self._docs[i]
                return type("R", (), {"deleted_count": 1})()
        return type("R", (), {"deleted_count": 0})()

    def delete_many(self, query: dict):
        before = len(self._docs)
        self._docs = [d for d in self._docs if not _matches(d, query)]
        return type("R", (), {"deleted_count": before - len(self._docs)})()

    def bulk_write(self, operations):
        for op in operations:
            self.update_one(op._filter, op._doc, upsert=op._upsert)

    def create_index(self, *args, **kwargs):
        return "index"

    def aggregate(self, pipeline):
        raise NotImplementedError(
            "FakeCollection does not implement aggregate. $vectorSearch is "
            "Atlas-only and cannot be emulated meaningfully."
        )


class FakeDB:
    """Dict of collections, created on demand."""

    def __init__(self):
        self._collections: dict[str, FakeCollection] = {}

    def __getitem__(self, name: str) -> FakeCollection:
        return self._collections.setdefault(name, FakeCollection(name))

    def reset(self):
        self._collections.clear()
