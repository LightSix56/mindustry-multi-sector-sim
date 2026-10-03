import re
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

LAUNCH_LINE_REGEX = re.compile(
    r"\[LAUNCH_EVENT\]\s+src:(?P<src>-?\d+)\s+dst:(?P<dst>-?\d+)\s+item:(?P<item>[a-zA-Z0-9_-]+)\s+amount:(?P<amount>\d+)"
)

# JS code injected into worker to capture launchpad events and print them
LAUNCH_LISTENER_JS = (
    "Events.on(EventType.LaunchItemEvent, cons(e => {"
    "  var s = Vars.state.rules.sector;"
    "  var src = s ? s.id : -1;"
    "  var dst = (s && s.info && s.info.destination) ? s.info.destination.id : -1;"
    "  var item = (e.stack && e.stack.item) ? e.stack.item.name : 'unknown';"
    "  var amt = e.stack ? e.stack.amount : 0;"
    "  print('[LAUNCH_EVENT] src:' + src + ' dst:' + dst + ' item:' + item + ' amount:' + amt);"
    "}));"
)


@dataclass
class LaunchEvent:
    source_sector: int
    target_sector: int
    item: str
    amount: int
    timestamp: float = field(default_factory=time.time)


class LaunchPadRouter:
    """Routes items launched from one sector's LaunchPad to another sector's Core."""

    def __init__(self):
        self._workers: Dict[int, Any] = {}
        self.pending_transfers: Dict[int, List[LaunchEvent]] = defaultdict(list)
        self.history: List[LaunchEvent] = []

    def register_worker(self, sector_id: int, worker: Any):
        self._workers[sector_id] = worker

    def unregister_worker(self, sector_id: int):
        self._workers.pop(sector_id, None)

    def parse_launch_line(self, line: str) -> Optional[LaunchEvent]:
        match = LAUNCH_LINE_REGEX.search(line)
        if not match:
            return None

        src = int(match.group("src"))
        dst = int(match.group("dst"))
        item = match.group("item")
        amount = int(match.group("amount"))

        return LaunchEvent(
            source_sector=src,
            target_sector=dst,
            item=item,
            amount=amount
        )

    def generate_deposit_script(self, item: str, amount: int) -> str:
        """Generates JS to deposit items into the team's core on the destination sector."""
        return (
            f'var core = Vars.state.teams.get(Team.sharded).core(); '
            f'if (core != null) {{ core.items.add(Vars.content.item("{item}"), {amount}); }}'
        )


    def route_transfer(self, event: LaunchEvent) -> bool:
        """Attempts to deliver to running worker, or queues for later delivery."""
        self.history.append(event)
        target_id = event.target_sector

        # If target worker is currently active and running
        worker = self._workers.get(target_id)
        if worker is not None:
            script = self.generate_deposit_script(event.item, event.amount)
            worker.send_js(script)
            return True

        # Otherwise queue in pending
        self.pending_transfers[target_id].append(event)
        return False

    def flush_pending(self, sector_id: int) -> int:
        """Flushes any pending cargo deliveries to a newly started worker."""
        worker = self._workers.get(sector_id)
        if worker is None:
            return 0

        pending = self.pending_transfers.pop(sector_id, [])
        delivered = 0
        for event in pending:
            script = self.generate_deposit_script(event.item, event.amount)
            worker.send_js(script)
            delivered += 1

        return delivered
