import os
import time
import sqlite3
import asyncio
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any

DB_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "telemetry.db")

class TelemetryDB:
    def __init__(self, db_path: str = DB_FILE):
        self.db_path = db_path
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, check_same_thread=False, timeout=15.0)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        """Initializes tables, indices, and WAL mode for high concurrency."""
        conn = self._get_conn()
        try:
            with conn:
                conn.execute("PRAGMA journal_mode = WAL;")
                conn.execute("PRAGMA synchronous = NORMAL;")
                
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS request_logs (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        request_id TEXT UNIQUE NOT NULL,
                        endpoint TEXT NOT NULL,
                        account_id TEXT NOT NULL,
                        model TEXT NOT NULL,
                        prompt TEXT,
                        prompt_preview TEXT,
                        status TEXT DEFAULT 'in_flight',
                        status_code INTEGER DEFAULT 200,
                        latency_seconds REAL DEFAULT 0.0,
                        response_preview TEXT,
                        response_full TEXT,
                        error_message TEXT,
                        prompt_tokens INTEGER DEFAULT 0,
                        completion_tokens INTEGER DEFAULT 0,
                        total_tokens INTEGER DEFAULT 0,
                        cost_savings_usd REAL DEFAULT 0.0,
                        created_at_ts REAL NOT NULL,
                        timestamp TEXT NOT NULL,
                        date_str TEXT NOT NULL,
                        hour_int INTEGER NOT NULL,
                        provider TEXT DEFAULT 'web'
                    );
                """)
                
                # Dynamic column check for backward compatibility
                try:
                    conn.execute("ALTER TABLE request_logs ADD COLUMN provider TEXT DEFAULT 'web';")
                except Exception:
                    pass

                # High-speed query indices
                conn.execute("CREATE INDEX IF NOT EXISTS idx_req_date ON request_logs(date_str);")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_req_ts ON request_logs(created_at_ts);")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_req_acc ON request_logs(account_id);")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_req_model ON request_logs(model);")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_req_status ON request_logs(status);")

                # Startup hygiene: nothing can still be in flight when the process boots, so any
                # 'in_flight' row is an orphan from a previous run (crash / restart mid-request).
                # Close it out so the dashboard never shows a phantom "SENDING..." row forever.
                conn.execute("""
                    UPDATE request_logs SET
                        status = 'error',
                        status_code = 499,
                        error_message = 'Interrupted: router restarted while this request was in flight'
                    WHERE status = 'in_flight'
                """)
        finally:
            conn.close()

    def log_start(
        self,
        request_id: str,
        endpoint: str,
        account_id: str,
        model: str,
        prompt: str,
        created_at_ts: Optional[float] = None,
        provider: str = "web"
    ):
        now_ts = created_at_ts or time.time()
        dt = datetime.fromtimestamp(now_ts)
        timestamp_str = dt.strftime("%H:%M:%S")
        date_str = dt.strftime("%Y-%m-%d")
        hour_int = dt.hour
        prompt_preview = (prompt[:300] + "...") if len(prompt) > 300 else prompt

        conn = self._get_conn()
        try:
            with conn:
                conn.execute("""
                    INSERT INTO request_logs (
                        request_id, endpoint, account_id, model, prompt, prompt_preview,
                        status, status_code, latency_seconds, created_at_ts, timestamp,
                        date_str, hour_int, provider
                    ) VALUES (?, ?, ?, ?, ?, ?, 'in_flight', 200, 0.0, ?, ?, ?, ?, ?)
                    ON CONFLICT(request_id) DO UPDATE SET
                        account_id = excluded.account_id,
                        model = excluded.model,
                        prompt = excluded.prompt,
                        prompt_preview = excluded.prompt_preview,
                        provider = excluded.provider
                """, (
                    request_id, endpoint, account_id, model, prompt, prompt_preview,
                    now_ts, timestamp_str, date_str, hour_int, provider
                ))
        except Exception as e:
            print(f"[TelemetryDB] Error in log_start: {e}")
        finally:
            conn.close()

    def log_finish(
        self,
        request_id: str,
        status: str,
        status_code: int,
        latency_seconds: float,
        response_preview: str = "",
        response_full: str = "",
        error_message: str = "",
        prompt: Optional[str] = None
    ):
        conn = self._get_conn()
        try:
            # Token and savings estimation
            p_text = prompt or ""
            r_text = response_full or response_preview or ""
            p_tokens = max(1, len(p_text) // 4) if p_text else 0
            c_tokens = max(1, len(r_text) // 4) if r_text else 0
            tot_tokens = p_tokens + c_tokens
            savings_usd = round((tot_tokens / 1_000_000.0) * 0.15 + 0.002, 4) if status == "success" else 0.0
            
            clean_preview = (response_preview[:300] + "...") if len(response_preview) > 300 else response_preview

            with conn:
                conn.execute("""
                    UPDATE request_logs SET
                        status = ?,
                        status_code = ?,
                        latency_seconds = ?,
                        response_preview = ?,
                        response_full = ?,
                        error_message = ?,
                        prompt_tokens = ?,
                        completion_tokens = ?,
                        total_tokens = ?,
                        cost_savings_usd = ?
                    WHERE request_id = ?
                """, (
                    status, status_code, round(latency_seconds, 2),
                    clean_preview, response_full, error_message,
                    p_tokens, c_tokens, tot_tokens, savings_usd,
                    request_id
                ))
        except Exception as e:
            print(f"[TelemetryDB] Error in log_finish: {e}")
        finally:
            conn.close()

    def cancel_inflight_for_account(self, account_id: Optional[str] = None):
        """Cancels in-flight requests in DB with status 499."""
        conn = self._get_conn()
        try:
            with conn:
                if account_id:
                    conn.execute("""
                        UPDATE request_logs SET
                            status = 'cancelled',
                            status_code = 499,
                            error_message = 'Request stopped / cancelled by user'
                        WHERE status = 'in_flight' AND account_id = ?
                    """, (account_id,))
                else:
                    conn.execute("""
                        UPDATE request_logs SET
                            status = 'cancelled',
                            status_code = 499,
                            error_message = 'Request stopped / cancelled by user'
                        WHERE status = 'in_flight'
                    """)
        except Exception as e:
            print(f"[TelemetryDB] Error in cancel_inflight_for_account: {e}")
        finally:
            conn.close()

    def get_logs(
        self,
        limit: int = 100,
        offset: int = 0,
        status_filter: Optional[str] = None,
        account_filter: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        search_query: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        conn = self._get_conn()
        try:
            query = "SELECT * FROM request_logs WHERE 1=1"
            params: List[Any] = []

            if status_filter and status_filter != "all":
                if status_filter == "error":
                    query += " AND status IN ('error', 'failed')"
                elif status_filter == "non_json":
                    query += " AND status = 'success' AND (response_preview NOT LIKE '%{%' AND response_preview NOT LIKE '%[%')"
                elif status_filter == "json":
                    query += " AND status = 'success' AND (response_preview LIKE '%{%' OR response_preview LIKE '%[%')"
                else:
                    query += " AND status = ?"
                    params.append(status_filter)

            if account_filter and account_filter != "all":
                query += " AND account_id = ?"
                params.append(account_filter)

            if date_from:
                query += " AND date_str >= ?"
                params.append(date_from)
            if date_to:
                query += " AND date_str <= ?"
                params.append(date_to)

            if search_query:
                query += " AND (request_id LIKE ? OR prompt LIKE ? OR response_preview LIKE ? OR model LIKE ?)"
                like_term = f"%{search_query}%"
                params.extend([like_term, like_term, like_term, like_term])

            query += " ORDER BY created_at_ts DESC LIMIT ? OFFSET ?"
            params.extend([limit, offset])

            cur = conn.cursor()
            cur.execute(query, params)
            rows = cur.fetchall()
            results = []
            for r in rows:
                d = dict(r)
                d["prompt_full"] = d.get("prompt") or d.get("prompt_preview") or ""
                if not d.get("response_full"):
                    d["response_full"] = d.get("response_preview") or ""
                results.append(d)
            return results
        except Exception as e:
            print(f"[TelemetryDB] Error in get_logs: {e}")
            return []
        finally:
            conn.close()

    def get_total_count(self) -> int:
        conn = self._get_conn()
        try:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM request_logs")
            return cur.fetchone()[0]
        except Exception:
            return 0
        finally:
            conn.close()

    def clear_logs(self):
        conn = self._get_conn()
        try:
            with conn:
                conn.execute("DELETE FROM request_logs;")
        except Exception as e:
            print(f"[TelemetryDB] Error in clear_logs: {e}")
        finally:
            conn.close()

    def compute_analytics(
        self,
        range_type: str = "today",
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        account_manager_accounts: Optional[Dict[str, Any]] = None,
        routing_meta: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Computes aggregated telemetry metrics, custom timeline charts,
        model distribution, account workloads, and formal daily reports
        from persistent database records.
        """
        now = datetime.now()
        today_str = now.strftime("%Y-%m-%d")
        
        # Determine date filters based on range_type
        filter_start_str = today_str
        filter_end_str = today_str
        range_label = f"Daily Report ({today_str})"
        is_hourly = True

        if range_type == "today":
            filter_start_str = today_str
            filter_end_str = today_str
            range_label = f"Today's Daily Report ({now.strftime('%b %d, %Y')})"
            is_hourly = True
        elif range_type == "yesterday":
            yest = now - timedelta(days=1)
            filter_start_str = yest.strftime("%Y-%m-%d")
            filter_end_str = yest.strftime("%Y-%m-%d")
            range_label = f"Yesterday's Report ({yest.strftime('%b %d, %Y')})"
            is_hourly = True
        elif range_type == "7d":
            past7 = now - timedelta(days=6)
            filter_start_str = past7.strftime("%Y-%m-%d")
            filter_end_str = today_str
            range_label = f"Weekly Report (Last 7 Days: {past7.strftime('%b %d')} - {now.strftime('%b %d')})"
            is_hourly = False
        elif range_type == "30d":
            past30 = now - timedelta(days=29)
            filter_start_str = past30.strftime("%Y-%m-%d")
            filter_end_str = today_str
            range_label = f"Monthly Report (Last 30 Days: {past30.strftime('%b %d')} - {now.strftime('%b %d')})"
            is_hourly = False
        elif range_type == "month":
            first_day_month = now.replace(day=1).strftime("%Y-%m-%d")
            filter_start_str = first_day_month
            filter_end_str = today_str
            range_label = f"Month-to-Date Report ({now.strftime('%B %Y')})"
            is_hourly = False
        elif range_type == "custom":
            filter_start_str = start_date or today_str
            filter_end_str = end_date or today_str
            range_label = f"Custom Report ({filter_start_str} to {filter_end_str})"
            # If custom range is single day, use hourly view
            is_hourly = (filter_start_str == filter_end_str)
        elif range_type == "all":
            filter_start_str = "1970-01-01"
            filter_end_str = "2099-12-31"
            range_label = "All-Time Lifetime Report"
            is_hourly = False

        conn = self._get_conn()
        try:
            cur = conn.cursor()

            # 1. Total Requests & Status Aggregation
            cur.execute("""
                SELECT 
                    COUNT(*) as total,
                    SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) as success,
                    SUM(CASE WHEN status IN ('error', 'failed') THEN 1 ELSE 0 END) as errors,
                    SUM(CASE WHEN status = 'cancelled' THEN 1 ELSE 0 END) as cancelled,
                    SUM(CASE WHEN status = 'in_flight' THEN 1 ELSE 0 END) as in_flight,
                    AVG(CASE WHEN latency_seconds > 0 THEN latency_seconds ELSE NULL END) as avg_latency,
                    SUM(prompt_tokens) as sum_p_tokens,
                    SUM(completion_tokens) as sum_c_tokens,
                    SUM(total_tokens) as sum_tokens,
                    SUM(cost_savings_usd) as sum_savings
                FROM request_logs
                WHERE date_str >= ? AND date_str <= ?
            """, (filter_start_str, filter_end_str))
            
            agg = cur.fetchone()
            total_reqs = agg["total"] or 0
            success_count = agg["success"] or 0
            error_count = agg["errors"] or 0
            cancelled_count = agg["cancelled"] or 0
            in_flight_count = agg["in_flight"] or 0
            avg_latency = round(agg["avg_latency"] or 0.0, 2)
            prompt_tokens = int(agg["sum_p_tokens"] or 0)
            completion_tokens = int(agg["sum_c_tokens"] or 0)
            total_tokens = int(agg["sum_tokens"] or 0)
            savings_usd = round(agg["sum_savings"] or 0.0, 3)

            # If tokens were not computed in older rows, derive from characters
            if total_tokens == 0 and total_reqs > 0:
                cur.execute("""
                    SELECT SUM(LENGTH(prompt)), SUM(LENGTH(response_preview))
                    FROM request_logs
                    WHERE date_str >= ? AND date_str <= ?
                """, (filter_start_str, filter_end_str))
                char_row = cur.fetchone()
                p_chars = char_row[0] or 0
                r_chars = char_row[1] or 0
                prompt_tokens = p_chars // 4
                completion_tokens = r_chars // 4
                total_tokens = prompt_tokens + completion_tokens
                savings_usd = round((total_tokens / 1_000_000.0) * 0.15 + (total_reqs * 0.002), 3)

            success_rate = round((success_count / max(1, total_reqs)) * 100.0, 1) if total_reqs > 0 else 100.0

            # 2. Timeline Histogram
            timeline: List[Dict[str, Any]] = []
            if is_hourly:
                # 24 hours of the day (00:00 to 23:00)
                cur.execute("""
                    SELECT hour_int, COUNT(*) as total,
                           SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) as success,
                           SUM(CASE WHEN status IN ('error', 'failed') THEN 1 ELSE 0 END) as error
                    FROM request_logs
                    WHERE date_str >= ? AND date_str <= ?
                    GROUP BY hour_int
                """, (filter_start_str, filter_end_str))
                hour_rows = {r["hour_int"]: r for r in cur.fetchall()}
                for h in range(24):
                    label = f"{h:02d}:00"
                    r = hour_rows.get(h)
                    timeline.append({
                        "time": label,
                        "total": r["total"] if r else 0,
                        "success": r["success"] if r else 0,
                        "error": r["error"] if r else 0
                    })
            else:
                # Day-by-day buckets
                cur.execute("""
                    SELECT date_str, COUNT(*) as total,
                           SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) as success,
                           SUM(CASE WHEN status IN ('error', 'failed') THEN 1 ELSE 0 END) as error
                    FROM request_logs
                    WHERE date_str >= ? AND date_str <= ?
                    GROUP BY date_str
                    ORDER BY date_str ASC
                """, (filter_start_str, filter_end_str))
                day_rows = {r["date_str"]: r for r in cur.fetchall()}
                
                # If custom or 7d/30d, enumerate dates
                try:
                    s_dt = datetime.strptime(filter_start_str, "%Y-%m-%d")
                    e_dt = datetime.strptime(filter_end_str, "%Y-%m-%d")
                    delta_days = min(60, (e_dt - s_dt).days + 1)
                    for d_i in range(delta_days):
                        cur_d = (s_dt + timedelta(days=d_i)).strftime("%Y-%m-%d")
                        short_lbl = (s_dt + timedelta(days=d_i)).strftime("%b %d")
                        r = day_rows.get(cur_d)
                        timeline.append({
                            "time": short_lbl,
                            "date": cur_d,
                            "total": r["total"] if r else 0,
                            "success": r["success"] if r else 0,
                            "error": r["error"] if r else 0
                        })
                except Exception:
                    for d_str, r in sorted(day_rows.items()):
                        timeline.append({
                            "time": d_str[5:],
                            "date": d_str,
                            "total": r["total"],
                            "success": r["success"],
                            "error": r["error"]
                        })

            # 3. Model Breakdown
            cur.execute("""
                SELECT model, COUNT(*) as total,
                       SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) as success,
                       AVG(CASE WHEN latency_seconds > 0 THEN latency_seconds ELSE NULL END) as avg_latency
                FROM request_logs
                WHERE date_str >= ? AND date_str <= ?
                GROUP BY model
                ORDER BY total DESC
            """, (filter_start_str, filter_end_str))
            models = []
            for r in cur.fetchall():
                models.append({
                    "model": r["model"],
                    "total": r["total"],
                    "success": r["success"] or 0,
                    "avg_latency": round(r["avg_latency"] or 0.0, 2),
                    "share_pct": round((r["total"] / max(1, total_reqs)) * 100.0, 1)
                })

            # 4. Account Distribution
            cur.execute("""
                SELECT account_id, COUNT(*) as total,
                       SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) as success,
                       AVG(CASE WHEN latency_seconds > 0 THEN latency_seconds ELSE NULL END) as avg_latency
                FROM request_logs
                WHERE date_str >= ? AND date_str <= ?
                GROUP BY account_id
                ORDER BY total DESC
            """, (filter_start_str, filter_end_str))
            
            accounts_summary = []
            account_db_rows = {r["account_id"]: r for r in cur.fetchall()}
            
            all_known_accounts = set(account_db_rows.keys())
            if account_manager_accounts:
                all_known_accounts.update(account_manager_accounts.keys())

            for acc_id in sorted(all_known_accounts):
                db_r = account_db_rows.get(acc_id)
                acc_obj = account_manager_accounts.get(acc_id) if account_manager_accounts else None
                
                tot = db_r["total"] if db_r else 0
                succ = db_r["success"] if db_r else 0
                avg_l = round(db_r["avg_latency"] or 0.0, 2) if db_r and db_r["avg_latency"] else (acc_obj.avg_latency if acc_obj else 0.0)

                accounts_summary.append({
                    "account_id": acc_id,
                    "total": tot,
                    "success": succ,
                    "avg_latency": avg_l,
                    "weekly_usage_pct": getattr(acc_obj, "weekly_usage_pct", None) if acc_obj else None,
                    "current_usage_pct": getattr(acc_obj, "current_usage_pct", None) if acc_obj else None,
                    "plan": getattr(acc_obj, "plan", "Free") if acc_obj else "Free",
                    "is_quota_protected": acc_obj.is_quota_protected() if acc_obj and hasattr(acc_obj, "is_quota_protected") else False,
                    "is_busy": getattr(acc_obj, "is_busy", False) if acc_obj else False,
                })
            accounts_summary.sort(key=lambda x: x["total"], reverse=True)

            # 5. Throughput RPM (for today: requests in last 5 minutes)
            recent_5m_count = 0
            if range_type in ("today", "yesterday"):
                cur.execute("""
                    SELECT COUNT(*) FROM request_logs
                    WHERE created_at_ts >= ?
                """, (time.time() - 300,))
                recent_5m_count = cur.fetchone()[0] or 0
            rpm = round(recent_5m_count / 5.0, 1)

            return {
                "status": "success",
                "range_type": range_type,
                "range_label": range_label,
                "filter_start": filter_start_str,
                "filter_end": filter_end_str,
                "is_hourly": is_hourly,
                "range_info": {
                    "type": range_type,
                    "label": range_label,
                    "start": filter_start_str,
                    "end": filter_end_str,
                    "is_hourly": is_hourly
                },
                "total_requests": total_reqs,
                "rpm": rpm,
                "avg_latency": avg_latency,
                "success_rate": success_rate,
                "status_counts": {
                    "success": success_count,
                    "error": error_count,
                    "cancelled": cancelled_count,
                    "in_flight": in_flight_count,
                    "warning": 0
                },
                "est_tokens": {
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                    "total_tokens": total_tokens,
                    "savings_usd": savings_usd
                },
                "timeline": timeline,
                "models": models,
                "accounts": accounts_summary,
                "routing": routing_meta or {
                    "strategy": "balanced",
                    "quota_guard_enabled": True,
                    "quota_threshold": 95
                }
            }
        except Exception as e:
            print(f"[TelemetryDB] Error in compute_analytics: {e}")
            return {"status": "error", "message": str(e)}
        finally:
            conn.close()

    def export_csv(self, start_date: Optional[str] = None, end_date: Optional[str] = None) -> str:
        """Generates RFC4180 CSV export of request logs for spreadsheet reporting."""
        import csv
        import io

        conn = self._get_conn()
        try:
            cur = conn.cursor()
            query = """
                SELECT request_id, date_str, timestamp, endpoint, COALESCE(provider, 'web') as provider, account_id,
                       model, status, status_code, latency_seconds,
                       prompt_tokens, completion_tokens, total_tokens, cost_savings_usd,
                       error_message
                FROM request_logs
                WHERE 1=1
            """
            params = []
            if start_date:
                query += " AND date_str >= ?"
                params.append(start_date)
            if end_date:
                query += " AND date_str <= ?"
                params.append(end_date)
            query += " ORDER BY created_at_ts DESC"

            cur.execute(query, params)
            rows = cur.fetchall()

            output = io.StringIO()
            writer = csv.writer(output)
            writer.writerow([
                "Request ID", "Date", "Time", "Endpoint", "Pool / Engine", "Account ID",
                "Model", "Status", "Status Code", "Latency (s)",
                "Prompt Tokens", "Completion Tokens", "Total Tokens", "Savings (USD)",
                "Error Message"
            ])
            for r in rows:
                writer.writerow(list(r))
            return output.getvalue()
        finally:
            conn.close()

# Singleton DB instance
telemetry_db = TelemetryDB()
