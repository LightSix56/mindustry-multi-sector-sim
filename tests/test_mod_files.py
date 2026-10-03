import json
import os
import shutil
from pathlib import Path
import pytest

MOD_SRC_DIR = Path("c:/antig/mods/multi-sector-sim")

def test_mod_json_validity():
    mod_json = MOD_SRC_DIR / "mod.json"
    assert mod_json.exists(), "mod.json must exist"
    data = json.loads(mod_json.read_text(encoding="utf-8"))
    assert data["name"] == "multi-sector-sim"
    assert "version" in data
    assert "displayName" in data
    assert data["minGameVersion"] == "146"

def test_main_js_syntax():
    main_js = MOD_SRC_DIR / "scripts" / "main.js"
    assert main_js.exists(), "main.js must exist"
    content = main_js.read_text(encoding="utf-8")
    assert "Events.on(ClientLoadEvent" in content
    assert "http://127.0.0.1:8765" in content
    assert "Vars.ui.join.connect" in content or "NetClient.connect" in content
    assert "Packages.arc.func.ConsT" in content, "Must use ConsT for Http.submit"
    assert "java.lang.String.valueOf" in content, "Must use java.lang.String.valueOf for URL"
    assert "boost" in content, "Must contain boost support"
    assert "goal" in content, "Must contain goal support"
    assert "ClientChatEvent" in content, "Must listen to ClientChatEvent for singleplayer/campaign commands"
    assert "timeout" in content, "Must configure timeout"
    assert "req.content = jContent" in content or 'req["content' in content, "Must handle content field/method safe assignment"

def test_mod_deployment(tmp_path):
    dest_mods = tmp_path / "mods" / "multi-sector-sim"
    shutil.copytree(MOD_SRC_DIR, dest_mods)
    assert (dest_mods / "mod.json").exists()
    assert (dest_mods / "scripts" / "main.js").exists()

def test_real_appdata_mod_deployment():
    appdata = os.environ.get("APPDATA")
    if appdata:
        target = Path(appdata) / "Mindustry" / "mods" / "multi-sector-sim" / "scripts" / "main.js"
        if target.exists():
            src_text = (MOD_SRC_DIR / "scripts" / "main.js").read_text(encoding="utf-8")
            dst_text = target.read_text(encoding="utf-8")
            assert src_text == dst_text, "AppData main.js must match source main.js exactly"
