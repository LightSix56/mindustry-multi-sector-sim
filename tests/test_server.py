import json
import threading
import urllib.request
from pathlib import Path
import pytest
from mindustry_sim.server import OrchestratorManager, OrchestratorServer
from mindustry_sim.saves import SectorMetadata

@pytest.fixture
def manager(tmp_path):
    saves_dir = tmp_path / "saves"
    saves_dir.mkdir()
    (saves_dir / "sector-serpulo-170.msav").write_bytes(b"dummy")
    (saves_dir / "sector-serpulo-64.msav").write_bytes(b"dummy")
    mgr = OrchestratorManager(
        save_dir=saves_dir,
        worker_base_dir=tmp_path / "workers",
        server_jar=tmp_path / "server.jar"
    )
    return mgr

def test_manager_get_sectors(manager):
    sectors = manager.get_sectors()
    assert len(sectors) == 2
    ids = [s["sector_id"] for s in sectors]
    assert 170 in ids
    assert 64 in ids
    assert all(s["running"] is False for s in sectors)

def test_server_http_endpoints(manager):
    # Find free port for test server
    port = 18765
    server = OrchestratorServer(manager, host="127.0.0.1", port=port)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    base_url = f"http://127.0.0.1:{port}"

    # Test GET /api/sectors
    req = urllib.request.Request(f"{base_url}/api/sectors")
    with urllib.request.urlopen(req) as resp:
        data = json.loads(resp.read().decode())
        assert resp.status == 200
        assert len(data["sectors"]) == 2

    # Test GET /api/status
    req = urllib.request.Request(f"{base_url}/api/status")
    with urllib.request.urlopen(req) as resp:
        data = json.loads(resp.read().decode())
        assert data["status"] == "online"

    # Test GET /api/sectors/170
    req = urllib.request.Request(f"{base_url}/api/sectors/170")
    with urllib.request.urlopen(req) as resp:
        data = json.loads(resp.read().decode())
        assert data["sector_id"] == 170
        assert data["running"] is False

    # Test POST /api/sectors/170/speed (not running)
    req = urllib.request.Request(
        f"{base_url}/api/sectors/170/speed",
        data=json.dumps({"speed": 5.0}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST"
    )
    with urllib.request.urlopen(req) as resp:
        data = json.loads(resp.read().decode())
        assert data["sector_id"] == 170
        assert "error" in data

    # Test POST /api/sectors/170/goal (not running)
    req = urllib.request.Request(
        f"{base_url}/api/sectors/170/goal",
        data=json.dumps({"target_wave": 20}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST"
    )
    with urllib.request.urlopen(req) as resp:
        data = json.loads(resp.read().decode())
        assert data["sector_id"] == 170
        assert "error" in data

    # Test speed and goal with an active mock worker
    class MockWorker:
        def __init__(self):
            self.speed = 1.0
            self.goal = None
            self.goal_reached = False
        def is_running(self):
            return True
        def set_speed(self, val):
            self.speed = val
        def set_goal(self, goal):
            self.goal = goal
            self.goal_reached = False

    mock = MockWorker()
    manager.workers[170] = mock

    # POST speed
    req = urllib.request.Request(
        f"{base_url}/api/sectors/170/speed",
        data=json.dumps({"speed": 10.0}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST"
    )
    with urllib.request.urlopen(req) as resp:
        data = json.loads(resp.read().decode())
        assert data["speed"] == 10.0
        assert mock.speed == 10.0

    # POST goal
    req = urllib.request.Request(
        f"{base_url}/api/sectors/170/goal",
        data=json.dumps({"target_wave": 50}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST"
    )
    with urllib.request.urlopen(req) as resp:
        data = json.loads(resp.read().decode())
        assert data["goal"] == {"target_wave": 50}
        assert mock.goal == {"target_wave": 50}

    # POST /api/player/enter
    req = urllib.request.Request(
        f"{base_url}/api/player/enter",
        data=json.dumps({"sector_id": 170}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST"
    )
    with urllib.request.urlopen(req) as resp:
        data = json.loads(resp.read().decode())
        assert resp.status == 200
        assert data["active_sector"] == 170

    # GET /api/player
    req = urllib.request.Request(f"{base_url}/api/player")
    with urllib.request.urlopen(req) as resp:
        data = json.loads(resp.read().decode())
        assert resp.status == 200
        assert data["active_sector"] == 170

    # POST /api/player/leave
    req = urllib.request.Request(
        f"{base_url}/api/player/leave",
        data=json.dumps({"sector_id": 170}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST"
    )
    with urllib.request.urlopen(req) as resp:
        data = json.loads(resp.read().decode())
        assert resp.status == 200
        assert data["active_sector"] is None

    server.shutdown()


def test_player_enter_leave_auto_suspend_resume(manager):
    # Setup mock worker for sector 170
    class ActiveWorker:
        def __init__(self):
            self.speed = 5.0
            self.goal = {"target_wave": 30}
            self.goal_reached = False
            self._running = True
            self.resources = {"copper": 150}

        def is_running(self):
            return self._running

        def save(self):
            pass

        def stop(self):
            self._running = False

        def status(self):
            return {
                "sector_id": 170,
                "running": self._running,
                "speed": self.speed,
                "goal": self.goal,
                "resources": self.resources
            }

    worker170 = ActiveWorker()
    manager.workers[170] = worker170

    # 1. Player enters sector 170: should suspend worker and remember settings and return resources
    res = manager.player_enter(170)
    assert res["active_sector"] == 170
    assert 170 in manager.suspended_sectors
    assert manager.suspended_sectors[170]["speed"] == 5.0
    assert manager.suspended_sectors[170]["goal"] == {"target_wave": 30}
    assert not worker170.is_running()
    assert manager.last_resources[170] == {"copper": 150}
    assert res.get("resources") == {"copper": 150}

    # Verify sector info reflects last_resources even when stopped
    sectors = manager.get_sectors()
    s170 = next(s for s in sectors if s["sector_id"] == 170)
    assert s170["running"] is False
    assert s170["resources"] == {"copper": 150}

    # 2. Player leaves sector 170: should trigger resume
    # Mock start_sector to observe resumption
    resumed = {}
    def mock_start_sector(sec_id):
        resumed["sector_id"] = sec_id
        w = ActiveWorker()
        manager.workers[sec_id] = w
        return w.status()

    manager.start_sector = mock_start_sector

    res_leave = manager.player_leave(170)
    assert res_leave["resumed"] == [170]
    assert manager.active_player_sector is None
    assert 170 not in manager.suspended_sectors
    assert resumed.get("sector_id") == 170
    assert manager.workers[170].speed == 5.0
    assert manager.workers[170].goal == {"target_wave": 30}


def test_discover_sectors_caching(manager, tmp_path):
    # Initial sectors count is 2
    sectors = manager.get_sectors()
    assert len(sectors) == 2

    # Add a new sector file directly to disk
    saves_dir = manager.save_dir
    (saves_dir / "sector-serpulo-99.msav").write_bytes(b"dummy")

    # Due to caching (TTL=5s), get_sectors() should still return 2 immediately
    cached_sectors = manager.get_sectors()
    assert len(cached_sectors) == 2

    # With force=True (or after invalidation), it discovers the new sector
    refreshed_sectors = manager.get_sectors(force=True)
    assert len(refreshed_sectors) == 3
    ids = [s["sector_id"] for s in refreshed_sectors]
    assert 99 in ids


