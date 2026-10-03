import pytest
from mindustry_sim.router import LaunchEvent, LaunchPadRouter

def test_parse_launch_event_line():
    router = LaunchPadRouter()
    line = "[LAUNCH_EVENT] src:170 dst:64 item:silicon amount:100"
    event = router.parse_launch_line(line)
    assert event is not None
    assert event.source_sector == 170
    assert event.target_sector == 64
    assert event.item == "silicon"
    assert event.amount == 100

def test_parse_invalid_line():
    router = LaunchPadRouter()
    assert router.parse_launch_line("[INFO] Server loaded.") is None
    assert router.parse_launch_line("[LAUNCH_EVENT] corrupt line") is None

def test_generate_deposit_script():
    router = LaunchPadRouter()
    script = router.generate_deposit_script("thorium", 50)
    assert 'Vars.content.item("thorium")' in script
    assert "50" in script
    assert "items.add" in script

def test_route_transfer_active_target():
    router = LaunchPadRouter()
    executed_cmds = []

    class MockWorker:
        def send_js(self, script):
            executed_cmds.append(script)

    worker_64 = MockWorker()
    router.register_worker(64, worker_64)

    event = LaunchEvent(source_sector=170, target_sector=64, item="titanium", amount=100)
    delivered = router.route_transfer(event)
    assert delivered is True
    assert len(executed_cmds) == 1
    assert 'content.item("titanium")' in executed_cmds[0]

def test_route_transfer_pending_target():
    router = LaunchPadRouter()
    event = LaunchEvent(source_sector=170, target_sector=999, item="lead", amount=200)
    delivered = router.route_transfer(event)
    assert delivered is False
    assert 999 in router.pending_transfers
    assert len(router.pending_transfers[999]) == 1

    # Now register target worker and flush
    executed_cmds = []
    class MockWorker:
        def send_js(self, script):
            executed_cmds.append(script)

    mock = MockWorker()
    router.register_worker(999, mock)
    flushed_count = router.flush_pending(999)
    assert flushed_count == 1
    assert len(executed_cmds) == 1
    assert len(router.pending_transfers[999]) == 0
