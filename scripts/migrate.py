#!/usr/bin/env python3
"""Database migration runner for crypto-agent.

Usage:
    python scripts/migrate.py [--dry-run] [--rollback VERSION]

Examples:
    # Apply all pending migrations
    python scripts/migrate.py

    # Preview migrations without applying
    python scripts/migrate.py --dry-run

    # Check migration status
    python scripts/migrate.py --status
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
from pathlib import Path

import pg8000


def get_connection() -> pg8000.Connection:
    """Create database connection from environment variables."""
    return pg8000.connect(
        host=os.getenv("POSTGRES_HOST", "localhost"),
        port=int(os.getenv("POSTGRES_PORT", "5432")),
        database=os.getenv("POSTGRES_DB", "marketdata"),
        user=os.getenv("POSTGRES_USER", "trading"),
        password=os.getenv("POSTGRES_PASSWORD", ""),
    )


def get_migrations_dir() -> Path:
    """Get migrations directory path."""
    return Path(__file__).parent.parent / "migrations"


def get_migration_files() -> list[tuple[str, Path]]:
    """Get sorted list of migration files."""
    migrations_dir = get_migrations_dir()
    files = []
    for path in sorted(migrations_dir.glob("*.sql")):
        version = path.stem.split("_")[0]
        files.append((version, path))
    return files


def get_file_checksum(path: Path) -> str:
    """Calculate SHA256 checksum of migration file."""
    content = path.read_text(encoding="utf-8")
    return hashlib.sha256(content.encode()).hexdigest()[:16]


def ensure_tracking_table(conn: pg8000.Connection) -> None:
    """Ensure migrations tracking table exists."""
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            applied_at TIMESTAMPTZ DEFAULT NOW(),
            checksum TEXT
        );
    """)
    conn.commit()


def get_applied_migrations(conn: pg8000.Connection) -> set[str]:
    """Get set of applied migration versions."""
    cursor = conn.cursor()
    cursor.execute("SELECT version FROM schema_migrations ORDER BY version")
    return {row[0] for row in cursor.fetchall()}


def apply_migration(
    conn: pg8000.Connection,
    version: str,
    path: Path,
    dry_run: bool = False,
) -> bool:
    """Apply a single migration."""
    content = path.read_text(encoding="utf-8")
    checksum = get_file_checksum(path)
    name = path.stem

    if dry_run:
        print(f"  [DRY-RUN] Would apply: {version} - {name}")
        return True

    cursor = conn.cursor()

    try:
        # Execute migration SQL
        cursor.execute(content)

        # Record migration
        cursor.execute(
            """
            INSERT INTO schema_migrations (version, name, checksum)
            VALUES (%s, %s, %s)
            ON CONFLICT (version) DO UPDATE SET
                name = EXCLUDED.name,
                checksum = EXCLUDED.checksum,
                applied_at = NOW()
            """,
            (version, name, checksum),
        )

        conn.commit()
        print(f"  Applied: {version} - {name}")
        return True

    except Exception as exc:
        conn.rollback()
        print(f"  FAILED: {version} - {name}")
        print(f"    Error: {exc}")
        return False


def run_migrations(dry_run: bool = False) -> int:
    """Run all pending migrations."""
    try:
        conn = get_connection()
    except Exception as exc:
        print(f"Failed to connect to database: {exc}")
        return 1

    ensure_tracking_table(conn)

    applied = get_applied_migrations(conn)
    migrations = get_migration_files()
    pending = [(v, p) for v, p in migrations if v not in applied]

    if not pending:
        print("No pending migrations.")
        return 0

    print(f"Found {len(pending)} pending migration(s):")

    for version, path in pending:
        if not apply_migration(conn, version, path, dry_run):
            return 1

    if dry_run:
        print("\nDry run complete. No changes made.")
    else:
        print("\nAll migrations applied successfully.")

    conn.close()
    return 0


def show_status() -> int:
    """Show migration status."""
    try:
        conn = get_connection()
    except Exception as exc:
        print(f"Failed to connect to database: {exc}")
        return 1

    ensure_tracking_table(conn)

    applied = get_applied_migrations(conn)
    migrations = get_migration_files()

    print("Migration Status:")
    print("-" * 60)

    for version, path in migrations:
        status = "APPLIED" if version in applied else "PENDING"
        checksum = get_file_checksum(path)
        print(f"  {version} | {status:8} | {path.stem} ({checksum})")

    print("-" * 60)
    print(
        f"Total: {len(migrations)} | Applied: {len(applied)} | Pending: {len(migrations) - len(applied)}"
    )

    conn.close()
    return 0


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(description="Database migration runner")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview migrations without applying",
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="Show migration status",
    )

    args = parser.parse_args()

    if args.status:
        return show_status()

    return run_migrations(dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
