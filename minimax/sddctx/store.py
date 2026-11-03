"""工程资料存储与检索（seekdb / OceanBase，MySQL 协议）。

规格规则、代码符号、测试用例与导入批次都保存在数据库中；检索阶段只通过 SQL
取数，取回的记录直接进入任务上下文。
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from pathlib import PurePosixPath
from typing import Any

import pymysql

from sddctx.config import DatabaseSettings
from sddctx.models import CodeSymbol, SpecRule, TestCase

SCHEMA_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS spec_rule (
        spec_id VARCHAR(64) NOT NULL,
        rule_id VARCHAR(96) NOT NULL,
        spec_title VARCHAR(255) NOT NULL,
        spec_status VARCHAR(32) NOT NULL,
        rule_title VARCHAR(255) NOT NULL,
        body TEXT NOT NULL,
        tags VARCHAR(255) NOT NULL DEFAULT '',
        source_file VARCHAR(255) NOT NULL,
        content_hash VARCHAR(64) NOT NULL,
        updated_at DATETIME NOT NULL,
        PRIMARY KEY (spec_id, rule_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS code_symbol (
        symbol VARCHAR(160) NOT NULL,
        kind VARCHAR(32) NOT NULL,
        file VARCHAR(255) NOT NULL,
        line INT NOT NULL,
        signature VARCHAR(255) NOT NULL,
        doc TEXT,
        body TEXT,
        content_hash VARCHAR(64) NOT NULL,
        updated_at DATETIME NOT NULL,
        PRIMARY KEY (symbol, file)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS test_case (
        name VARCHAR(160) NOT NULL,
        file VARCHAR(255) NOT NULL,
        line INT NOT NULL,
        doc TEXT,
        body TEXT,
        targets VARCHAR(512) NOT NULL DEFAULT '',
        content_hash VARCHAR(64) NOT NULL,
        updated_at DATETIME NOT NULL,
        PRIMARY KEY (name, file)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS import_batch (
        batch_id VARCHAR(64) NOT NULL,
        imported_at DATETIME NOT NULL,
        source_dir VARCHAR(255) NOT NULL,
        spec_count INT NOT NULL,
        code_count INT NOT NULL,
        test_count INT NOT NULL,
        PRIMARY KEY (batch_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS import_record (
        batch_id VARCHAR(64) NOT NULL,
        material_kind VARCHAR(32) NOT NULL,
        material_key VARCHAR(255) NOT NULL,
        content_hash VARCHAR(64) NOT NULL,
        change_type VARCHAR(32) NOT NULL,
        PRIMARY KEY (batch_id, material_kind, material_key)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
)


class SeekDBStore:
    """工程资料库。"""

    def __init__(self, settings: DatabaseSettings) -> None:
        self._settings = settings
        self._connection: pymysql.Connection | None = None

    # -- 连接管理 -------------------------------------------------------------

    def _open(self, database: str | None) -> pymysql.Connection:
        return pymysql.connect(
            host=self._settings.host,
            port=self._settings.port,
            user=self._settings.user,
            password=self._settings.password,
            database=database,
            charset="utf8mb4",
            autocommit=True,
            connect_timeout=10,
        )

    def connect(self) -> pymysql.Connection:
        if self._connection is None:
            try:
                self._connection = self._open(self._settings.database)
            except pymysql.err.OperationalError as error:
                if not error.args or error.args[0] != 1049:
                    raise
                self._connection = self._open(None)
        return self._connection

    def close(self) -> None:
        if self._connection is not None:
            self._connection.close()
            self._connection = None

    def __enter__(self) -> SeekDBStore:
        self.connect()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def _query(self, sql: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]:
        with self.connect().cursor(pymysql.cursors.DictCursor) as cursor:
            cursor.execute(sql, params)
            return list(cursor.fetchall())

    def _execute(self, sql: str, params: Sequence[Any] = ()) -> int:
        with self.connect().cursor() as cursor:
            return cursor.execute(sql, params)

    def ensure_schema(self) -> None:
        """建库建表，保证空环境可以直接初始化。"""
        bootstrap = self._open(None)
        try:
            with bootstrap.cursor() as cursor:
                cursor.execute(
                    f"CREATE DATABASE IF NOT EXISTS `{self._settings.database}` "
                    "DEFAULT CHARACTER SET utf8mb4"
                )
        finally:
            bootstrap.close()
        if self._connection is not None:
            self._connection.close()
            self._connection = None
        self.connect()
        for statement in SCHEMA_STATEMENTS:
            self._execute(statement)

    # -- 写入 -----------------------------------------------------------------

    def upsert_spec_rules(self, rules: Iterable[SpecRule]) -> list[tuple[str, str]]:
        """写入规格规则，返回 (规则编号, 变更类型)。"""
        changes: list[tuple[str, str]] = []
        for rule in rules:
            existing = self._query(
                "SELECT content_hash FROM spec_rule WHERE spec_id = %s AND rule_id = %s",
                (rule.spec_id, rule.rule_id),
            )
            change_type = "inserted" if not existing else (
                "updated" if existing[0]["content_hash"] != rule.content_hash else "unchanged"
            )
            self._execute(
                """
                INSERT INTO spec_rule
                    (spec_id, rule_id, spec_title, spec_status, rule_title, body, tags,
                     source_file, content_hash, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
                ON DUPLICATE KEY UPDATE
                    spec_title = VALUES(spec_title),
                    spec_status = VALUES(spec_status),
                    rule_title = VALUES(rule_title),
                    body = VALUES(body),
                    tags = VALUES(tags),
                    source_file = VALUES(source_file),
                    content_hash = VALUES(content_hash),
                    updated_at = NOW()
                """,
                (
                    rule.spec_id,
                    rule.rule_id,
                    rule.spec_title,
                    rule.spec_status,
                    rule.rule_title,
                    rule.body,
                    ",".join(rule.tags),
                    rule.source_file,
                    rule.content_hash,
                ),
            )
            changes.append((rule.rule_id, change_type))
        return changes

    def upsert_code_symbols(self, symbols: Iterable[CodeSymbol]) -> list[tuple[str, str]]:
        """写入代码符号，返回 (符号, 变更类型)。"""
        changes: list[tuple[str, str]] = []
        for symbol in symbols:
            content_hash = _content_hash(symbol.file, symbol.symbol, symbol.body)
            existing = self._query(
                "SELECT content_hash FROM code_symbol WHERE symbol = %s AND file = %s",
                (symbol.symbol, symbol.file),
            )
            change_type = "inserted" if not existing else (
                "updated" if existing[0]["content_hash"] != content_hash else "unchanged"
            )
            self._execute(
                """
                INSERT INTO code_symbol
                    (symbol, kind, file, line, signature, doc, body, content_hash, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW())
                ON DUPLICATE KEY UPDATE
                    kind = VALUES(kind),
                    line = VALUES(line),
                    signature = VALUES(signature),
                    doc = VALUES(doc),
                    body = VALUES(body),
                    content_hash = VALUES(content_hash),
                    updated_at = NOW()
                """,
                (
                    symbol.symbol,
                    symbol.kind,
                    symbol.file,
                    symbol.line,
                    symbol.signature,
                    symbol.doc,
                    symbol.body,
                    content_hash,
                ),
            )
            changes.append((f"{symbol.file}::{symbol.symbol}", change_type))
        return changes

    def upsert_test_cases(self, tests: Iterable[TestCase]) -> list[tuple[str, str]]:
        """写入测试用例，返回 (用例, 变更类型)。"""
        changes: list[tuple[str, str]] = []
        for test in tests:
            content_hash = _content_hash(test.file, test.name, test.body)
            existing = self._query(
                "SELECT content_hash FROM test_case WHERE name = %s AND file = %s",
                (test.name, test.file),
            )
            change_type = "inserted" if not existing else (
                "updated" if existing[0]["content_hash"] != content_hash else "unchanged"
            )
            self._execute(
                """
                INSERT INTO test_case
                    (name, file, line, doc, body, targets, content_hash, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, NOW())
                ON DUPLICATE KEY UPDATE
                    line = VALUES(line),
                    doc = VALUES(doc),
                    body = VALUES(body),
                    targets = VALUES(targets),
                    content_hash = VALUES(content_hash),
                    updated_at = NOW()
                """,
                (
                    test.name,
                    test.file,
                    test.line,
                    test.doc,
                    test.body,
                    ",".join(test.targets),
                    content_hash,
                ),
            )
            changes.append((f"{test.file}::{test.name}", change_type))
        return changes

    def purge_stale_spec_rules(self, rules: Iterable[SpecRule]) -> list[str]:
        """删除工程里已经不存在的规格规则，返回被删除的规则编号。"""
        rules = list(rules)
        if not rules:
            return []
        clause, params = _scope_clause("source_file", _scopes(rule.source_file for rule in rules))
        rows = self._query(f"SELECT spec_id, rule_id FROM spec_rule WHERE {clause}", params)
        current = {(rule.spec_id, rule.rule_id) for rule in rules}
        removed: list[str] = []
        for row in rows:
            key = (row["spec_id"], row["rule_id"])
            if key in current:
                continue
            self._execute("DELETE FROM spec_rule WHERE spec_id = %s AND rule_id = %s", key)
            removed.append(f"{key[0]}::{key[1]}")
        return removed

    def purge_stale_code_symbols(self, symbols: Iterable[CodeSymbol]) -> list[str]:
        """删除工程里已经不存在的代码符号，返回被删除的符号标识。"""
        symbols = list(symbols)
        if not symbols:
            return []
        clause, params = _scope_clause("file", _scopes(symbol.file for symbol in symbols))
        rows = self._query(f"SELECT symbol, file FROM code_symbol WHERE {clause}", params)
        current = {(symbol.file, symbol.symbol) for symbol in symbols}
        removed: list[str] = []
        for row in rows:
            key = (row["file"], row["symbol"])
            if key in current:
                continue
            self._execute("DELETE FROM code_symbol WHERE symbol = %s AND file = %s", (key[1], key[0]))
            removed.append(f"{key[0]}::{key[1]}")
        return removed

    def purge_stale_test_cases(self, tests: Iterable[TestCase]) -> list[str]:
        """删除工程里已经不存在的测试用例，返回被删除的用例标识。"""
        tests = list(tests)
        if not tests:
            return []
        clause, params = _scope_clause("file", _scopes(test.file for test in tests))
        rows = self._query(f"SELECT name, file FROM test_case WHERE {clause}", params)
        current = {(test.file, test.name) for test in tests}
        removed: list[str] = []
        for row in rows:
            key = (row["file"], row["name"])
            if key in current:
                continue
            self._execute("DELETE FROM test_case WHERE name = %s AND file = %s", (key[1], key[0]))
            removed.append(f"{key[0]}::{key[1]}")
        return removed

    def record_batch(
        self,
        batch_id: str,
        source_dir: str,
        spec_count: int,
        code_count: int,
        test_count: int,
        records: Iterable[tuple[str, str, str, str]],
    ) -> None:
        """记录一次导入批次与每条资料的变更类型。"""
        self._execute(
            """
            INSERT INTO import_batch (batch_id, imported_at, source_dir, spec_count, code_count, test_count)
            VALUES (%s, NOW(), %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE imported_at = NOW()
            """,
            (batch_id, source_dir, spec_count, code_count, test_count),
        )
        for material_kind, material_key, content_hash, change_type in records:
            self._execute(
                """
                INSERT INTO import_record (batch_id, material_kind, material_key, content_hash, change_type)
                VALUES (%s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    content_hash = VALUES(content_hash),
                    change_type = VALUES(change_type)
                """,
                (batch_id, material_kind, material_key, content_hash, change_type),
            )

    # -- 检索 -----------------------------------------------------------------

    def search_spec_rules(
        self,
        tokens: Sequence[str],
        *,
        limit: int = 12,
        spec_ids: Sequence[str] = (),
        include_inactive: bool = False,
    ) -> list[tuple[SpecRule, int]]:
        """按关键词检索规格规则，命中词数作为得分。

        过滤条件是「命中任意一个关键词」，得分是命中的关键词个数，
        因此既不会漏掉部分命中的规则，也能把完全命中的规则排在前面。
        """
        score_params: list[Any] = []
        where_params: list[Any] = []
        score_parts: list[str] = []
        where_groups: list[str] = []

        if spec_ids:
            placeholders = ", ".join(["%s"] * len(spec_ids))
            where_groups.append(f"spec_id IN ({placeholders})")
            where_params.extend(spec_ids)
        if not include_inactive:
            where_groups.append("spec_status = 'active'")

        searchable = ("body", "rule_title", "spec_title", "tags")
        for token in tokens:
            for column in searchable:
                score_parts.append(f"CASE WHEN {column} LIKE %s THEN 1 ELSE 0 END")
                score_params.append(f"%{token}%")
        if tokens:
            token_conditions = []
            for token in tokens:
                for column in searchable:
                    token_conditions.append(f"{column} LIKE %s")
                    where_params.append(f"%{token}%")
            where_groups.append("(" + " OR ".join(token_conditions) + ")")

        if not score_parts:
            score_parts.append("1")

        where_clause = " AND ".join(where_groups) if where_groups else "1 = 1"
        sql = f"""
            SELECT spec_id, rule_id, spec_title, spec_status, rule_title, body, tags,
                   source_file, content_hash,
                   ({' + '.join(score_parts)}) AS score
            FROM spec_rule
            WHERE {where_clause}
            ORDER BY score DESC, spec_id, rule_id
            LIMIT %s
        """
        rows = self._query(sql, [*score_params, *where_params, limit])
        return [(_to_spec_rule(row), int(row["score"])) for row in rows]

    def search_code_symbols(
        self,
        tokens: Sequence[str],
        *,
        limit: int = 12,
        names: Sequence[str] = (),
    ) -> list[tuple[CodeSymbol, int]]:
        """按关键词与符号名检索代码符号。"""
        score_params: list[Any] = []
        where_params: list[Any] = []
        score_parts: list[str] = []
        where_groups: list[str] = []

        if names:
            name_conditions = []
            for name in names:
                name_conditions.append("(symbol = %s OR symbol LIKE %s)")
                where_params.extend((name, f"%.{name}"))
                score_params.extend((name, name))
            where_groups.append("(" + " OR ".join(name_conditions) + ")")
            score_parts.append(
                "(" + " + ".join("CASE WHEN symbol = %s OR symbol LIKE %s THEN 5 ELSE 0 END" for _ in names) + ")"
            )

        if tokens:
            token_conditions = []
            for token in tokens:
                for column in ("symbol", "signature", "doc"):
                    score_parts.append(f"CASE WHEN {column} LIKE %s THEN 1 ELSE 0 END")
                    score_params.append(f"%{token}%")
                    token_conditions.append(f"{column} LIKE %s")
                    where_params.append(f"%{token}%")
            where_groups.append("(" + " OR ".join(token_conditions) + ")")

        if not score_parts:
            score_parts.append("1")

        where_clause = " AND ".join(where_groups) if where_groups else "1 = 1"
        sql = f"""
            SELECT symbol, kind, file, line, signature, doc, body, content_hash,
                   ({' + '.join(score_parts)}) AS score
            FROM code_symbol
            WHERE {where_clause}
            ORDER BY score DESC, file, line
            LIMIT %s
        """
        rows = self._query(sql, [*score_params, *where_params, limit])
        return [(_to_code_symbol(row), int(row["score"])) for row in rows]

    def search_test_cases(
        self,
        tokens: Sequence[str],
        *,
        limit: int = 20,
        target_symbols: Sequence[str] = (),
    ) -> list[tuple[TestCase, int]]:
        """检索与任务相关的测试用例。"""
        score_params: list[Any] = []
        where_params: list[Any] = []
        score_parts: list[str] = []
        where_groups: list[str] = []

        if target_symbols:
            symbol_conditions = []
            for symbol in target_symbols:
                symbol_conditions.append("targets LIKE %s")
                where_params.append(f"%{symbol}%")
                score_parts.append("CASE WHEN targets LIKE %s THEN 5 ELSE 0 END")
                score_params.append(f"%{symbol}%")
            where_groups.append("(" + " OR ".join(symbol_conditions) + ")")

        if tokens:
            token_conditions = []
            for token in tokens:
                for column in ("name", "doc", "targets"):
                    score_parts.append(f"CASE WHEN {column} LIKE %s THEN 1 ELSE 0 END")
                    score_params.append(f"%{token}%")
                    token_conditions.append(f"{column} LIKE %s")
                    where_params.append(f"%{token}%")
            where_groups.append("(" + " OR ".join(token_conditions) + ")")

        if not score_parts:
            score_parts.append("1")

        where_clause = " AND ".join(where_groups) if where_groups else "1 = 1"
        sql = f"""
            SELECT name, file, line, doc, body, targets, updated_at,
                   ({' + '.join(score_parts)}) AS score
            FROM test_case
            WHERE {where_clause}
            ORDER BY score DESC, file, line
            LIMIT %s
        """
        rows = self._query(sql, [*score_params, *where_params, limit])
        return [(_to_test_case(row), int(row["score"])) for row in rows]

    def list_spec_rules(self, spec_id: str | None = None) -> list[SpecRule]:
        """列出规格规则。"""
        if spec_id:
            rows = self._query(
                "SELECT spec_id, rule_id, spec_title, spec_status, rule_title, body, tags,"
                " source_file, content_hash FROM spec_rule WHERE spec_id = %s ORDER BY rule_id",
                (spec_id,),
            )
        else:
            rows = self._query(
                "SELECT spec_id, rule_id, spec_title, spec_status, rule_title, body, tags,"
                " source_file, content_hash FROM spec_rule ORDER BY spec_id, rule_id"
            )
        return [_to_spec_rule(row) for row in rows]

    def list_test_cases(self) -> list[TestCase]:
        """列出全部测试用例。"""
        rows = self._query(
            "SELECT name, file, line, doc, body, targets, updated_at FROM test_case"
            " ORDER BY file, line"
        )
        return [_to_test_case(row) for row in rows]

    def list_code_symbols(self) -> list[CodeSymbol]:
        """列出全部代码符号。"""
        rows = self._query(
            "SELECT symbol, kind, file, line, signature, doc, body, content_hash FROM code_symbol"
            " ORDER BY file, line"
        )
        return [_to_code_symbol(row) for row in rows]

    def import_batches(self, limit: int = 20) -> list[dict[str, Any]]:
        """最近的导入批次。"""
        return self._query(
            "SELECT batch_id, imported_at, source_dir, spec_count, code_count, test_count"
            " FROM import_batch ORDER BY imported_at DESC LIMIT %s",
            (limit,),
        )

    def import_records(self, batch_id: str) -> list[dict[str, Any]]:
        """某个导入批次的逐条变更记录。"""
        return self._query(
            "SELECT material_kind, material_key, content_hash, change_type FROM import_record"
            " WHERE batch_id = %s ORDER BY material_kind, material_key",
            (batch_id,),
        )

    def counts(self) -> dict[str, int]:
        """资料统计。"""
        result: dict[str, int] = {}
        for table in ("spec_rule", "code_symbol", "test_case", "import_batch"):
            rows = self._query(f"SELECT COUNT(*) AS total FROM {table}")
            result[table] = int(rows[0]["total"])
        return result


def _content_hash(*parts: str) -> str:
    import hashlib

    digest = hashlib.sha256()
    digest.update("\x1f".join(parts).encode("utf-8"))
    return digest.hexdigest()


def _scopes(paths: Iterable[str]) -> set[str]:
    """资料所在的相对目录，用来界定「同一份工程资料」的范围。"""
    return {PurePosixPath(path).parent.as_posix() for path in paths}


def _scope_clause(column: str, scopes: set[str]) -> tuple[str, list[Any]]:
    """构造目录范围过滤条件。"""
    parts: list[str] = []
    params: list[Any] = []
    for scope in sorted(scopes):
        if scope in ("", "."):
            parts.append(f"{column} NOT LIKE %s")
            params.append("%/%")
        else:
            parts.append(f"{column} LIKE %s")
            params.append(f"{scope}/%")
    return "(" + " OR ".join(parts) + ")", params


def _to_spec_rule(row: dict[str, Any]) -> SpecRule:
    return SpecRule(
        spec_id=row["spec_id"],
        rule_id=row["rule_id"],
        spec_title=row["spec_title"],
        spec_status=row["spec_status"],
        rule_title=row["rule_title"],
        body=row["body"],
        tags=tuple(part for part in (row.get("tags") or "").split(",") if part),
        source_file=row["source_file"],
        content_hash=row["content_hash"],
    )


def _to_code_symbol(row: dict[str, Any]) -> CodeSymbol:
    return CodeSymbol(
        symbol=row["symbol"],
        kind=row["kind"],
        file=row["file"],
        line=int(row["line"]),
        signature=row["signature"],
        doc=row.get("doc") or "",
        body=row.get("body") or "",
    )


def _to_test_case(row: dict[str, Any]) -> TestCase:
    return TestCase(
        name=row["name"],
        file=row["file"],
        line=int(row["line"]),
        doc=row.get("doc") or "",
        body=row.get("body") or "",
        targets=tuple(part for part in (row.get("targets") or "").split(",") if part),
    )
