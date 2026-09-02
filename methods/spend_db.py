#!/usr/bin/python3
# coding=utf-8

#   Copyright 2025 EPAM Systems
#
#   Licensed under the Apache License, Version 2.0 (the "License");
#   you may not use this file except in compliance with the License.
#   You may obtain a copy of the License at
#
#       http://www.apache.org/licenses/LICENSE-2.0
#
#   Unless required by applicable law or agreed to in writing, software
#   distributed under the License is distributed on an "AS IS" BASIS,
#   WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#   See the License for the specific language governing permissions and
#   limitations under the License.

""" Method """

import time
import calendar
import datetime
import threading
from urllib.parse import urlparse, urlunparse

from sqlalchemy import create_engine, text  # pylint: disable=E0401

from pylon.core.tools import log  # pylint: disable=E0611,E0401,W0611
from pylon.core.tools import web  # pylint: disable=E0611,E0401,W0611

from .budgets import BUDGET_TAG_PREFIX, user_id_from_tag


# How long a project's member breakdown is served without re-reading the spend table.
# Paging and searching run over this snapshot, so changing page costs nothing.
MEMBER_SPEND_TTL = 60.0

# Only ever one connection string (the LiteLLM database), so a single cached engine
# rather than the keyed cache used for project-supplied pgvector connections.
_SPEND_ENGINE = None
_SPEND_ENGINE_URL = None
_SPEND_ENGINE_LOCK = threading.Lock()


def escape_like(value):
    r"""Escape a literal for use inside a LIKE pattern with ESCAPE '\'.

    Budget tags are full of underscores, which LIKE would otherwise read as
    single-character wildcards.
    """
    return str(value).replace("\\", "\\\\").replace("_", "\\_").replace("%", "\\%")


def make_member_tag_pattern(project_id, period):
    """LIKE pattern matching every per-member budget tag of one project and period."""
    prefix = escape_like(f"{BUDGET_TAG_PREFIX}{project_id}_user_")
    #
    return f"{prefix}%{escape_like('_' + period)}"


def period_bounds(period):
    """First and last calendar day of a YYYYMM period, as ISO date strings.

    The spend table stores date as text, so ISO strings compare correctly and let the
    date index bound the scan.
    """
    year, month = int(period[:4]), int(period[4:6])
    last_day = calendar.monthrange(year, month)[1]
    #
    return f"{year:04d}-{month:02d}-01", f"{year:04d}-{month:02d}-{last_day:02d}"


# Bounded on date and filtered with LIKE, never with a tag range. Under a non-C collation
# punctuation is ignored when comparing, so tag < 'elitea_proj_3_user~' collates equal to
# the lower bound and silently matches nothing at all. LIKE does not depend on collation.
MEMBER_SPEND_QUERY = text(r"""
    SELECT tag,
           SUM(spend) AS spend,
           SUM(api_requests) AS requests,
           SUM(successful_requests) AS successful_requests,
           SUM(failed_requests) AS failed_requests,
           SUM(prompt_tokens) AS prompt_tokens,
           SUM(completion_tokens) AS completion_tokens
    FROM "LiteLLM_DailyTagSpend"
    WHERE date >= :period_start AND date <= :period_end
      AND (tag = :project_tag OR tag LIKE :member_pattern ESCAPE '\')
    GROUP BY tag
""")


class Method:  # pylint: disable=E1101,R0903,W0201
    """ Method Resource """

    @web.method()
    def spend_db_url(self):
        """Connection string for the LiteLLM database, or None if it cannot be built."""
        mode = self.descriptor.config.get("litellm_database_mode", "elitea")
        #
        if mode != "elitea":
            return self.descriptor.config.get("database_url") or None
        #
        # Same derivation the runtime engine uses: swap only the database name, so the
        # credentials and host never have to be configured twice.
        try:
            from tools import config as c  # pylint: disable=E0401,C0415
            base_uri = c.DATABASE_URI
        except (ImportError, RuntimeError, AttributeError):
            base_uri = None
        #
        if not base_uri:
            return None
        #
        db_name = self.descriptor.config.get("litellm_db_name", "litellm")
        #
        return urlunparse(urlparse(base_uri)._replace(path=f"/{db_name}"))

    @web.method()
    def spend_db_engine(self):
        """Cached engine for the LiteLLM database, or None if it is not configured."""
        global _SPEND_ENGINE, _SPEND_ENGINE_URL  # pylint: disable=W0603
        #
        url = self.spend_db_url()
        #
        if not url:
            return None
        #
        with _SPEND_ENGINE_LOCK:
            # Rebuilt when an admin repoints the database at runtime
            if _SPEND_ENGINE is not None and _SPEND_ENGINE_URL == url:
                return _SPEND_ENGINE
            #
            stale = _SPEND_ENGINE
            #
            cfg = self.descriptor.config.get("spend_db") or {}
            #
            _SPEND_ENGINE = create_engine(
                url,
                pool_recycle=3600,
                pool_pre_ping=True,
                pool_size=max(1, int(cfg.get("pool_size", 2))),
                max_overflow=max(0, int(cfg.get("max_overflow", 3))),
            )
            _SPEND_ENGINE_URL = url
        #
        if stale is not None:
            try:
                stale.dispose()
            except:  # pylint: disable=W0702
                log.exception("Failed to dispose the previous spend database engine")
        #
        return _SPEND_ENGINE

    @web.method()
    def read_project_member_spend(self, project_id, period=None):
        """Every member of a project that actually spent, plus the project total.

        Enumerated from recorded spend rather than from current membership, so a member who
        has since left still appears and the rows reconcile to the project total. Returns
        None when the spend database cannot be read, so callers can fall back.
        """
        if period is None:
            period = f"{datetime.datetime.now(datetime.timezone.utc):%Y%m}"
        #
        cache = self.runtime_cache.setdefault("member_spend", {})
        cached = cache.get((project_id, period))
        #
        if cached is not None and time.monotonic() - cached[0] < MEMBER_SPEND_TTL:
            return cached[1]
        #
        result = self.query_project_member_spend(project_id, period)
        #
        # Failures are not cached: a briefly unreachable database should not pin the
        # project to the fallback list for a whole TTL.
        if result is not None:
            cache[(project_id, period)] = (time.monotonic(), result)
        #
        return result

    @web.method()
    def query_project_member_spend(self, project_id, period):
        """One grouped read of the spend table for a project and period."""
        engine = self.spend_db_engine()
        #
        if engine is None:
            log.warning("Spend database is not configured; per-member spend unavailable")
            return None
        #
        period_start, period_end = period_bounds(period)
        project_tag = f"{BUDGET_TAG_PREFIX}{project_id}_{period}"
        #
        try:
            with engine.connect() as connection:
                rows = connection.execute(
                    MEMBER_SPEND_QUERY,
                    {
                        "period_start": period_start,
                        "period_end": period_end,
                        "project_tag": project_tag,
                        "member_pattern": make_member_tag_pattern(project_id, period),
                    },
                ).fetchall()
        except:  # pylint: disable=W0702
            log.exception("Failed to read per-member spend for project %s", project_id)
            return None
        #
        members = {}
        project = None
        #
        for row in rows:
            entry = {
                "spend": float(row.spend or 0),
                "requests": int(row.requests or 0),
                "successful_requests": int(row.successful_requests or 0),
                "failed_requests": int(row.failed_requests or 0),
                "input_tokens": int(row.prompt_tokens or 0),
                "output_tokens": int(row.completion_tokens or 0),
            }
            #
            if row.tag == project_tag:
                project = entry
                continue
            #
            user_id = user_id_from_tag(row.tag)
            #
            if user_id is not None:
                members[user_id] = entry
        #
        return {
            "period": period,
            "members": members,
            "project": project or {"spend": 0.0, "requests": 0},
        }

    @web.method()
    def invalidate_member_spend_cache(self, project_id=None):
        """Drop cached breakdowns, for one project or all of them."""
        cache = self.runtime_cache.get("member_spend", None)
        #
        if not cache:
            return
        #
        if project_id is None:
            cache.clear()
            return
        #
        for key in [key for key in cache if key[0] == project_id]:
            cache.pop(key, None)
