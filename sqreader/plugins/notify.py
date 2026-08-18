"""Get an alert in front of a human.

Every plugin can already raise an alert, and every alert already lands in
`plugin_alerts`. Nothing ever read that table. The detection worked and the
delivery did not exist, so the only way to learn about a cheater was to open
SQLite — which means nobody learned about anything.

This closes that. It wraps the `emit_alert` the manager injects, so delivery
belongs to the ALERT PIPELINE rather than to any one detector: write a new
plugin tomorrow and it gets announced without knowing this file exists.

Three things it must never do, in order of how badly they would hurt:

  * **Cost a tick.** Plugins run inside the reader loop. A webhook is a network
    call to someone else's server and can hang for thirty seconds. So the tick
    thread only ever puts a dict on a bounded queue; a worker thread does the
    talking. When the queue is full the alert is dropped and counted, never
    waited on. A missed notification is bad; a stalled reader is worse, and the
    alert is already durable in the database either way.
  * **Take the reader down.** Nothing here raises into the caller. Discord
    being down, DNS failing, a proxy eating the request — logged and forgotten.
  * **Leak the URL.** A webhook URL is a credential: whoever holds it can post
    into that channel. It is never logged, never echoed, never put in an error
    message.
"""
from __future__ import annotations

import json
import logging
import queue
import threading
import time
import urllib.error
import urllib.request
from typing import Any, Callable, Optional

log = logging.getLogger(__name__)

#: How many alerts may wait for delivery. Small on purpose: if the worker is
#: this far behind then Discord is down or rate-limiting us, and hoarding
#: thousands of stale alerts to dump later helps nobody.
QUEUE_MAX = 200

#: Discord allows ten embeds per message. Batching is what makes a burst — a
#: cheater tripping four detectors in one second — arrive as a single readable
#: post instead of four that race the rate limiter.
MAX_EMBEDS = 10

#: How long the worker waits for more alerts before posting what it has.
BATCH_WINDOW_SEC = 2.0

#: Colour by how sure the detector is. Red does not mean "bad", it means
#: "confident": someone scanning the channel should see at a glance which rows
#: are worth opening.
COLOUR_SURE = 0xE04F4F
COLOUR_MAYBE = 0xE0A93B
COLOUR_INFO = 0x6C8EBF


def colour_for(confidence: Optional[float]) -> int:
    if not isinstance(confidence, (int, float)):
        return COLOUR_INFO
    if confidence >= 0.8:
        return COLOUR_SURE
    if confidence >= 0.5:
        return COLOUR_MAYBE
    return COLOUR_INFO


def replay_url(base: Optional[str], match_id: Optional[str]) -> Optional[str]:
    """A link to the recording the alert came from.

    It opens the replay, not the moment: the viewer reads only `mode` and `id`
    from the URL, so there is nowhere to put a timestamp yet. The match time
    goes in the message instead and an admin scrubs to it. Inventing a `&t=`
    the viewer ignores would look like it worked and quietly not.
    """
    if not base or not match_id:
        return None
    return f"{base.rstrip('/')}/replay/?mode=replay&id={match_id}"


def match_clock(details: Any) -> Optional[str]:
    """`elapsedSec` as mm:ss, when the detector recorded it."""
    v = details.get("elapsedSec") if isinstance(details, dict) else None
    if not isinstance(v, (int, float)) or v < 0:
        return None
    s = int(v)
    return f"{s // 60}:{s % 60:02d}"


def build_embed(alert: dict, *, replay_base: Optional[str] = None,
                server_label: Optional[str] = None) -> dict:
    """One alert as a Discord embed. Pure — this is the part worth testing."""
    details = alert.get("details") or {}
    name = alert.get("player_name") or alert.get("eos_id") or "unknown player"
    conf = alert.get("confidence")
    title = f"{alert.get('alert_type', 'alert')} - {name}"

    lines: list[str] = []
    if isinstance(conf, (int, float)):
        lines.append(f"**confidence** {round(conf * 100)}%")
    clock = match_clock(details)
    if clock:
        lines.append(f"**match time** {clock}")
    if alert.get("eos_id"):
        lines.append("**eos** `" + str(alert["eos_id"]) + "`")
    # The evidence the detector based the call on, verbatim. An alert without
    # it is an accusation; with it, an admin can disagree.
    ev = {k: v for k, v in details.items() if k != "elapsedSec"} \
        if isinstance(details, dict) else {}
    if ev:
        body = json.dumps(ev, ensure_ascii=False, sort_keys=True)
        if len(body) > 900:
            body = body[:897] + "..."
        lines.append("```json\n" + body + "\n```")
    url = replay_url(replay_base, alert.get("match_id"))
    if url:
        lines.append(f"[watch the replay]({url})")

    embed: dict[str, Any] = {
        "title": title[:250],
        "description": "\n".join(lines)[:3900],
        "color": colour_for(conf),
    }
    footer = " - ".join(x for x in (server_label, alert.get("plugin_id")) if x)
    if footer:
        embed["footer"] = {"text": footer[:2000]}
    ts = alert.get("ts")
    if isinstance(ts, (int, float)):
        embed["timestamp"] = (time.strftime("%Y-%m-%dT%H:%M:%S",
                                            time.gmtime(ts)) + "Z")
    return embed


class DiscordNotifier:
    """Posts alerts to a Discord webhook, off the reader's thread."""

    def __init__(self, webhook_url: str, *, replay_base: Optional[str] = None,
                 server_label: Optional[str] = None,
                 min_confidence: float = 0.0,
                 timeout: float = 10.0) -> None:
        self._url = webhook_url
        self._replay_base = replay_base
        self._server_label = server_label
        self._min_conf = min_confidence
        self._timeout = timeout
        self._q: "queue.Queue[dict]" = queue.Queue(maxsize=QUEUE_MAX)
        self._stop = threading.Event()
        self.dropped = 0
        self._worker = threading.Thread(target=self._run, name="alert-notify",
                                        daemon=True)
        self._worker.start()

    # -- the tick side: cheap, bounded, never blocks -----------------------

    def wants(self, alert: dict) -> bool:
        c = alert.get("confidence")
        return not (isinstance(c, (int, float)) and c < self._min_conf)

    def offer(self, alert: dict) -> None:
        if not self.wants(alert):
            return
        try:
            self._q.put_nowait(alert)
        except queue.Full:
            # Dropping is the right failure: the alert is already durable in
            # `plugin_alerts`, and blocking the reader to announce it would
            # trade the recording for a notification.
            self.dropped += 1
            if self.dropped in (1, 10, 100) or self.dropped % 1000 == 0:
                log.warning("alert webhook backed up, dropped %d so far",
                            self.dropped)

    def wrap(self, emit: Callable[[dict], None]) -> Callable[[dict], None]:
        """Storing comes first and always happens; announcing is best effort."""
        def emit_and_notify(alert: dict) -> None:
            try:
                emit(alert)
            finally:
                try:
                    self.offer(alert)
                except Exception:                    # never reaches a plugin
                    log.exception("alert notify failed")
        return emit_and_notify

    def close(self, drain_sec: float = 3.0) -> None:
        self._stop.set()
        self._worker.join(timeout=drain_sec)

    # -- the worker side: allowed to be slow -------------------------------

    def _run(self) -> None:
        while not self._stop.is_set() or not self._q.empty():
            batch = self._collect()
            if batch:
                self._post(batch)

    def _collect(self) -> list[dict]:
        try:
            first = self._q.get(timeout=0.5)
        except queue.Empty:
            return []
        batch = [first]
        deadline = time.monotonic() + BATCH_WINDOW_SEC
        while len(batch) < MAX_EMBEDS:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                batch.append(self._q.get(timeout=remaining))
            except queue.Empty:
                break
        return batch

    def _post(self, batch: list[dict]) -> None:
        payload = json.dumps({
            "embeds": [build_embed(a, replay_base=self._replay_base,
                                   server_label=self._server_label)
                       for a in batch],
        }).encode("utf-8")
        for attempt in range(3):
            try:
                req = urllib.request.Request(
                    self._url, data=payload, method="POST",
                    headers={"Content-Type": "application/json",
                             "User-Agent": "sqreader"})
                with urllib.request.urlopen(req, timeout=self._timeout):
                    return
            except urllib.error.HTTPError as e:
                if e.code == 429:
                    # Discord says how long to wait; honour it rather than
                    # hammering a channel that is already refusing us.
                    wait = 1.0
                    try:
                        wait = float(json.loads(e.read() or b"{}")
                                     .get("retry_after", 1.0))
                    except Exception:
                        pass
                    if self._stop.wait(min(max(wait, 0.5), 30.0)):
                        return
                    continue
                # The URL is a credential — log the code and reason only.
                log.warning("alert webhook rejected: HTTP %s %s",
                            e.code, e.reason)
                return
            except Exception as e:                   # DNS, TLS, timeout, ...
                if attempt == 2:
                    log.warning("alert webhook unreachable: %s",
                                type(e).__name__)
                    return
                if self._stop.wait(1.0 + attempt):
                    return


__all__ = ["DiscordNotifier", "build_embed", "colour_for", "match_clock",
           "replay_url", "QUEUE_MAX", "MAX_EMBEDS"]
