// Multi-Sector Simulator Client Mod for Mindustry
var ORCHESTRATOR_URL = "http://127.0.0.1:8765";
var simDialog = null;
var listTable = null;
var listLoading = false;
var orchestratorLaunching = false;
var autoRefreshTask = null;

var ITEM_NAMES = {
    "copper": "Медь",
    "lead": "Свинец",
    "silicon": "Кремний",
    "graphite": "Графит",
    "titanium": "Титан",
    "thorium": "Торий",
    "plastanium": "Пласт",
    "phase-fabric": "Фаза",
    "surge-alloy": "Кинетит",
    "metaglass": "Стекло",
    "sand": "Песок",
    "coal": "Уголь",
    "pyratite": "Пиротит",
    "blast-compound": "Взрывчатка",
    "spore-pod": "Споры",
    "beryllium": "Бериллий",
    "tungsten": "Вольфрам",
    "oxide": "Оксид",
    "carbide": "Карбид",
    "fissile-matter": "Делящееся в-во",
    "dormant-cyst": "Спящая циста"
};

function formatResources(resources) {
    if (!resources) return "";
    var parts = [];
    for (var k in resources) {
        if (resources.hasOwnProperty(k)) {
            var val = resources[k];
            if (val > 0) {
                var name = ITEM_NAMES[k];
                if (!name) {
                    try {
                        if (Vars.content) {
                            var it = Vars.content.item(k);
                            if (it && it.localizedName) {
                                name = String(it.localizedName);
                            }
                        }
                    } catch (e) {}
                }
                if (!name) name = k;
                parts.push(name + ": " + val);
            }
        }
    }
    if (parts.length === 0) return "";
    return " | [lightgray]" + parts.join(", ") + "[]";
}

function notifyUser(msg, player) {
    print("[multi-sector-sim:client] notifyUser: " + msg);
    if (player && typeof player.sendMessage === "function") {
        player.sendMessage(msg);
    }
    Core.app.post(run(function() {
        try {
            if (Vars.ui && Vars.ui.hudfrag && Vars.ui.hudfrag.showToast) {
                Vars.ui.hudfrag.showToast(msg);
            } else if (Vars.ui && Vars.ui.showInfoToast) {
                Vars.ui.showInfoToast(msg, 3);
            }
            if (Vars.ui && Vars.ui.chatfrag && Vars.ui.chatfrag.addMessage) {
                Vars.ui.chatfrag.addMessage(msg);
            }
        } catch (e) {
            print("[multi-sector-sim:client] notify error: " + e);
        }
    }));
}

function fetchJson(endpoint, method, arg3, arg4, arg5) {
    var callback = null;
    var errorCallback = null;
    var body = null;

    if (typeof arg3 === "function") {
        callback = arg3;
        errorCallback = arg4;
        body = null;
    } else {
        body = arg3;
        callback = arg4;
        errorCallback = arg5;
    }

    try {
        var urlStr = java.lang.String.valueOf(ORCHESTRATOR_URL + endpoint);
        print("[multi-sector-sim:client] HTTP IPC -> " + method + " " + endpoint + (body ? " body=" + JSON.stringify(body) : ""));
        var req = (method === "POST") ? Packages.arc.util.Http.post(urlStr) : Packages.arc.util.Http.get(urlStr);

        // Mindustry Arc HttpRequest timeout (90s for /start, 45s for /save, 30s default)
        var reqTimeout = 30000;
        if (endpoint.indexOf("/start") !== -1) {
            reqTimeout = 90000;
        } else if (endpoint.indexOf("/save") !== -1) {
            reqTimeout = 45000;
        }
        if (typeof req["timeout(int)"] === "function") {
            req["timeout(int)"](reqTimeout);
        } else {
            req.timeout = reqTimeout;
        }

        if (body && method === "POST") {
            req.header("Content-Type", "application/json");
            var contentStr = (typeof body === "string") ? body : JSON.stringify(body);
            var jContent = java.lang.String.valueOf(contentStr);
            if (typeof req["content(java.lang.String)"] === "function") {
                req["content(java.lang.String)"](jContent);
            } else {
                req.content = jContent;
            }
        }

        req.error(cons(function(err) {
            print("[multi-sector-sim:client] HTTP IPC error for " + urlStr + ": " + err);
            Core.app.post(run(function() { if (errorCallback) errorCallback(err); }));
        }));

        req.submit(new Packages.arc.func.ConsT({
            get: function(res) {
                try {
                    var raw = res.getResultAsString();
                    print("[multi-sector-sim:client] HTTP IPC <- " + method + " " + endpoint + " (" + raw.length + " bytes)");
                    var json = JSON.parse(raw);
                    Core.app.post(run(function() { if (callback) callback(json); }));
                } catch (e) {
                    print("[multi-sector-sim:client] JSON parse/callback error: " + e);
                    Core.app.post(run(function() { if (errorCallback) errorCallback(e); }));
                }
            }
        }));
    } catch (e) {
        print("[multi-sector-sim:client] Direct HTTP call error: " + e);
        if (errorCallback) errorCallback(e);
    }
}

function launchOrchestrator() {
    if (orchestratorLaunching) return false;
    orchestratorLaunching = true;
    try {
        print("[multi-sector-sim:client] Launching orchestrator via start_sim.bat...");
        new java.lang.ProcessBuilder("cmd.exe", "/c", "start", "c:\\antig\\start_sim.bat").start();
        Timer.schedule(run(function() { orchestratorLaunching = false; }), 8.0);
        return true;
    } catch (e) {
        orchestratorLaunching = false;
        print("[multi-sector-sim:client] Launch error: " + e);
        return false;
    }
}

function applySpeed(secId, mult, player) {
    print("[multi-sector-sim:client] Applying speed " + mult + "x to sector " + secId);
    fetchJson("/api/sectors/" + secId + "/speed", "POST", { speed: mult }, function(res) {
        if (res.error) {
            notifyUser("[scarlet][multi-sector] Сектор " + secId + ": " + res.error + "[]", player);
        } else {
            notifyUser("[yellow][multi-sector] ⚡ Сектор " + secId + " ускорен до " + mult + "x![]", player);
        }
    }, function(err) {
        notifyUser("[scarlet][multi-sector] Ошибка /boost: " + err + "[]", player);
    });
}

function applyGoal(secId, targetWave, player) {
    print("[multi-sector-sim:client] Applying goal wave " + targetWave + " to sector " + secId);
    fetchJson("/api/sectors/" + secId + "/goal", "POST", { target_wave: targetWave }, function(res) {
        if (res.error) {
            notifyUser("[scarlet][multi-sector] Сектор " + secId + ": " + res.error + "[]", player);
        } else {
            notifyUser("[cyan][multi-sector] 🎯 Сектор " + secId + ": цель установлена на волну " + targetWave + "![]", player);
        }
    }, function(err) {
        notifyUser("[scarlet][multi-sector] Ошибка /goal: " + err + "[]", player);
    });
}

function handleSimCommand(cmdName, args, player) {
    print("[multi-sector-sim:client] Command entered: /" + cmdName + " args=" + JSON.stringify(args));
    if (cmdName === "sim") {
        Core.app.post(run(function() { showSimDialog(); }));
        return true;
    }

    if (cmdName === "boost") {
        if (!args || args.length === 0 || args[0] === "") {
            fetchJson("/api/sectors", "GET", function(data) {
                var running = (data.sectors || []).filter(function(s) { return s.running; });
                if (running.length === 0) {
                    notifyUser("[yellow][multi-sector] Нет активных запущенных секторов. Использование: /boost <скорость> [сектор][]", player);
                } else {
                    var msgs = running.map(function(s) { return "Сектор " + s.sector_id + ": " + (s.speed || 1) + "x"; });
                    notifyUser("[yellow][multi-sector] Активная скорость: " + msgs.join(", ") + "[]", player);
                }
            }, function(err) {
                notifyUser("[scarlet][multi-sector] Оркестратор недоступен: " + err + "[]", player);
            });
            return true;
        }

        var mult = parseFloat(args[0]) || 1.0;
        var specifiedSecId = (args.length > 1 && !isNaN(parseInt(args[1]))) ? parseInt(args[1]) : null;

        if (specifiedSecId !== null) {
            applySpeed(specifiedSecId, mult, player);
        } else {
            var curSec = (Vars.state && Vars.state.rules && Vars.state.rules.sector) ? Vars.state.rules.sector.id : null;
            fetchJson("/api/sectors", "GET", function(data) {
                var running = (data.sectors || []).filter(function(s) { return s.running; });
                var targetId = null;
                if (curSec !== null && running.some(function(s) { return s.sector_id === curSec; })) {
                    targetId = curSec;
                } else if (running.length === 1) {
                    targetId = running[0].sector_id;
                } else if (curSec !== null) {
                    targetId = curSec;
                } else if (running.length > 0) {
                    targetId = running[0].sector_id;
                } else {
                    targetId = 170;
                }
                applySpeed(targetId, mult, player);
            }, function() {
                applySpeed(curSec || 170, mult, player);
            });
        }
        return true;
    }

    if (cmdName === "goal") {
        if (!args || args.length === 0 || args[0] === "") {
            fetchJson("/api/sectors", "GET", function(data) {
                var running = (data.sectors || []).filter(function(s) { return s.running; });
                if (running.length === 0) {
                    notifyUser("[cyan][multi-sector] Нет активных запущенных секторов. Использование: /goal <волна> [сектор][]", player);
                } else {
                    var msgs = running.map(function(s) {
                        var gStr = s.goal ? ("волна " + s.goal.target_wave + (s.goal_reached ? " (выполнено!)" : "")) : "нет";
                        return "Сектор " + s.sector_id + ": цель=" + gStr;
                    });
                    notifyUser("[cyan][multi-sector] Активные цели: " + msgs.join(", ") + "[]", player);
                }
            }, function(err) {
                notifyUser("[scarlet][multi-sector] Оркестратор недоступен: " + err + "[]", player);
            });
            return true;
        }

        var target = parseInt(args[0]) || 50;
        var specifiedSecId = (args.length > 1 && !isNaN(parseInt(args[1]))) ? parseInt(args[1]) : null;

        if (specifiedSecId !== null) {
            applyGoal(specifiedSecId, target, player);
        } else {
            var curSec = (Vars.state && Vars.state.rules && Vars.state.rules.sector) ? Vars.state.rules.sector.id : null;
            fetchJson("/api/sectors", "GET", function(data) {
                var running = (data.sectors || []).filter(function(s) { return s.running; });
                var targetId = null;
                if (curSec !== null && running.some(function(s) { return s.sector_id === curSec; })) {
                    targetId = curSec;
                } else if (running.length === 1) {
                    targetId = running[0].sector_id;
                } else if (curSec !== null) {
                    targetId = curSec;
                } else if (running.length > 0) {
                    targetId = running[0].sector_id;
                } else {
                    targetId = 170;
                }
                applyGoal(targetId, target, player);
            }, function() {
                applyGoal(curSec || 170, target, player);
            });
        }
        return true;
    }

    return false;
}

function connectToSector(port) {
    print("[multi-sector-sim:client] Connecting to sector on port " + port);
    var currentName = (Vars.player && Vars.player.name) ? String(Vars.player.name).trim() : "";
    if (!currentName) {
        currentName = String(Core.settings.getString("name", "")).trim();
    }
    if (!currentName) {
        currentName = "Player";
    }

    if (Vars.player) Vars.player.name = currentName;
    Core.settings.put("name", currentName);
    try { Core.settings.saveValues(); } catch (e) {}

    // Auto-save current game before connecting so user doesn't lose progress
    if (Vars.state && Vars.state.isGame()) {
        try {
            Vars.control.saves.saveCurrent();
        } catch (e) {
            print("[multi-sector-sim:client] saveCurrent error: " + e);
        }
    }

    if (simDialog) simDialog.hide();
    stopAutoRefresh();
    Vars.ui.join.connect("127.0.0.1", port);
}

function startAutoRefresh() {
    stopAutoRefresh();
    print("[multi-sector-sim:client] Starting live auto-refresh timer (every 3.0s)...");
    try {
        autoRefreshTask = Timer.schedule(run(function() {
            if (!simDialog || !simDialog.isShown()) {
                stopAutoRefresh();
                return;
            }
            fetchJson("/api/sectors", "GET", null, function(data) {
                if (simDialog && simDialog.isShown() && data && data.sectors) {
                    renderSectors(data.sectors);
                }
            }, function(err) {
                print("[multi-sector-sim:client] Auto-refresh fetch error: " + err);
            });
        }), 3.0, 3.0);
    } catch (e) {
        print("[multi-sector-sim:client] Failed to start auto-refresh timer: " + e);
    }
}

function stopAutoRefresh() {
    if (autoRefreshTask) {
        print("[multi-sector-sim:client] Stopping live auto-refresh timer.");
        try {
            autoRefreshTask.cancel();
        } catch (e) {}
        autoRefreshTask = null;
    }
}

function ensureSimDialog() {
    if (simDialog) return;

    simDialog = new BaseDialog("[accent]Симуляция секторов (Multi-Sector Sim)[]");
    simDialog.addCloseButton();

    try {
        simDialog.hidden(run(function() {
            print("[multi-sector-sim:client] simDialog closed, stopping auto-refresh.");
            stopAutoRefresh();
        }));
    } catch (e) {
        print("[multi-sector-sim:client] hidden hook error: " + e);
    }

    var cont = simDialog.cont;
    cont.clear();

    cont.add("[lightgray]Параллельная симуляция выбранных секторов в фоне[]").padBottom(10).row();

    listTable = new Table();
    cont.pane(listTable).grow().pad(10).row();

    var buttons = cont.table().bottom().get();
    buttons.button("[accent]Обновить[]", Icon.refresh, function() {
        print("[multi-sector-sim:client] User clicked Refresh button");
        rebuildList(true);
    }).size(150, 50).pad(6);
}

function renderSectors(sectors) {
    if (!listTable) return;
    listTable.clear();

    if (sectors.length === 0) {
        listTable.add("[scarlet]Сектора не найдены![]").row();
        listTable.add("[lightgray]Убедитесь, что у вас есть сохранения в кампании[]").padTop(8).row();
        return;
    }

    var currentActiveSectorId = (Vars.state && Vars.state.isGame() && Vars.state.rules && Vars.state.rules.sector) ? Vars.state.rules.sector.id : null;

    sectors.forEach(function(sec) {
        var secTable = new Table(Tex.buttonEdge3);
        secTable.left().margin(10);

        var title = "[accent]Сектор " + sec.sector_id + "[] (" + sec.planet + ")";
        secTable.add(title).left().width(180);

        // Active sector conflict check: player is currently playing this sector
        var isCurrentSector = (currentActiveSectorId !== null && sec.sector_id === currentActiveSectorId);

        if (isCurrentSector) {
            // Read REAL-TIME live core items directly from active game engine
            var activeItems = {};
            try {
                var playerCore = null;
                if (Vars.player && Vars.player.team()) {
                    playerCore = Vars.player.team().core();
                }
                if (!playerCore && Vars.state && Vars.state.rules && Vars.state.rules.defaultTeam) {
                    playerCore = Vars.state.rules.defaultTeam.core();
                }
                if (playerCore && playerCore.items) {
                    Vars.content.items().each(cons(function(it) {
                        var count = playerCore.items.get(it);
                        if (count > 0) activeItems[it.name] = count;
                    }));
                }
            } catch (eCore) {
                print("[multi-sector-sim:client] error reading active core items: " + eCore);
            }

            var hasActive = Object.keys(activeItems).length > 0;
            var resSummary = formatResources(hasActive ? activeItems : (sec.resources || {}));
            var activeText = "[gold]★ ВЫ НА ЭТОМ СЕКТОРЕ[]" + resSummary;
            secTable.add(activeText).left().growX().padLeft(10);

            var activeBtn = secTable.button("[gold]В игре[]", function() {
                // Clicking allows user to cancel auto-resumption if desired
                fetchJson("/api/sectors/" + sec.sector_id + "/stop", "POST", null, function() {
                    notifyUser("[lightgray]Авто-возобновление для сектора " + sec.sector_id + " отменено[]", null);
                    rebuildList(true);
                });
            }).size(130, 42).tooltip("Вы находитесь на этом секторе (симуляция авто-возобновится при выходе). Нажмите, чтобы отменить.").get();

        } else if (sec.running) {
            var speedStr = (sec.speed && sec.speed > 1) ? (" [yellow]⚡" + sec.speed + "x[]") : "";
            var goalStr = "";
            if (sec.goal) {
                if (sec.goal_reached) {
                    goalStr = " [green]🎯Цель выполнена![]";
                } else if (sec.goal.target_wave) {
                    goalStr = " [cyan]🎯Цель: в." + sec.goal.target_wave + "[]";
                }
            }

            var waveTimeStr = (sec.wavetime !== undefined && sec.wavetime > 0) ? (" (" + Math.round(sec.wavetime) + "с)") : "";
            var resSummary = formatResources(sec.resources);

            var statusText = "[green]● РАБОТАЕТ[] (В." + sec.wave + waveTimeStr + ", П." + sec.port + speedStr + goalStr + ")" + resSummary;
            secTable.add(statusText).left().growX().padLeft(10);

            // Speed Boost toggle button (1x -> 2x -> 5x -> 10x -> 1x)
            var currentSpeed = sec.speed || 1;
            var speedLabel = "[yellow]" + currentSpeed + "x[]";
            secTable.button(speedLabel, function() {
                var nextSpeed = (currentSpeed === 1) ? 2 : (currentSpeed === 2) ? 5 : (currentSpeed === 5) ? 10 : 1;
                print("[multi-sector-sim:client] Toggling speed for sector " + sec.sector_id + " to " + nextSpeed + "x");
                fetchJson("/api/sectors/" + sec.sector_id + "/speed", "POST", { speed: nextSpeed }, function() {
                    notifyUser("[yellow]Сектор " + sec.sector_id + ": скорость " + nextSpeed + "x[]", null);
                    rebuildList(true);
                });
            }).size(55, 42).padRight(4).tooltip("Переключить ускорение симуляции (/boost)");

            // Goal button
            secTable.button(Icon.info, function() {
                var defaultWave = (sec.goal && sec.goal.target_wave) ? String(sec.goal.target_wave) : String(sec.wave + 20);
                print("[multi-sector-sim:client] Opening goal input for sector " + sec.sector_id);
                try {
                    Vars.ui.showTextInput(
                        java.lang.String.valueOf("[accent]Цель симуляции (Goal)[]"),
                        java.lang.String.valueOf("Введите целевую волну для сектора " + sec.sector_id + ":"),
                        10,
                        java.lang.String.valueOf(defaultWave),
                        cons(function(val) {
                            var target = parseInt(val);
                            if (!isNaN(target) && target > 0) {
                                print("[multi-sector-sim:client] Setting goal target_wave=" + target + " for sector " + sec.sector_id);
                                fetchJson("/api/sectors/" + sec.sector_id + "/goal", "POST", { target_wave: target }, function() {
                                    notifyUser("[cyan]Цель сектора " + sec.sector_id + ": волна " + target + "[]", null);
                                    rebuildList(true);
                                });
                            }
                        })
                    );
                } catch (e) {
                    print("[multi-sector-sim:client] showTextInput goal error: " + e);
                }
            }).size(48, 42).padRight(4).tooltip("Установить цель волны (/goal)");

            // Save button
            secTable.button(Icon.save, function() {
                print("[multi-sector-sim:client] User clicked Save for sector " + sec.sector_id);
                fetchJson("/api/sectors/" + sec.sector_id + "/save", "POST", null, function(res) {
                    print("[multi-sector-sim:client] Sector " + sec.sector_id + " save response: " + JSON.stringify(res));
                    notifyUser("[green]Сектор " + sec.sector_id + " сохранен на диск![]", null);
                }, function(err) {
                    print("[multi-sector-sim:client] Sector " + sec.sector_id + " save error: " + err);
                    notifyUser("[scarlet]Ошибка сохранения сектора " + sec.sector_id + ": " + err + "[]", null);
                });
            }).size(48, 42).padRight(4).tooltip("Сохранить прогресс сектора на диск");

            // Stop button
            secTable.button(Icon.cancel, function() {
                print("[multi-sector-sim:client] User clicked Stop for sector " + sec.sector_id);
                fetchJson("/api/sectors/" + sec.sector_id + "/stop", "POST", null, function() {
                    print("[multi-sector-sim:client] Sector " + sec.sector_id + " stopped.");
                    rebuildList(true);
                });
            }).size(48, 42).tooltip("Остановить фоновую симуляцию");

        } else {
            var stoppedRes = sec.resources;
            if (!stoppedRes || Object.keys(stoppedRes).length === 0) {
                stoppedRes = {};
                try {
                    var planet = Vars.content.planet(sec.planet) || Vars.content.planets().find(function(p) { return String(p.name) === String(sec.planet); });
                    if (planet && planet.sectors) {
                        var secObj = planet.sectors.get(sec.sector_id);
                        if (secObj && secObj.info && secObj.info.items) {
                            secObj.info.items.each(cons(function(st) {
                                if (st.amount > 0) stoppedRes[st.item.name] = st.amount;
                            }));
                        }
                    }
                } catch (eSec) {}
            }
            var resSummary = formatResources(stoppedRes);
            secTable.add("[gray]○ Остановлен[]" + resSummary).left().growX().padLeft(10);

            secTable.button("[green]Запустить[]", function() {
                print("[multi-sector-sim:client] User clicked Start for sector " + sec.sector_id);
                if (listTable) {
                    listTable.clear();
                    listTable.add("[golden]Запуск сектора " + sec.sector_id + "... подождите[]").row();
                }
                fetchJson("/api/sectors/" + sec.sector_id + "/start", "POST", null, function() {
                    print("[multi-sector-sim:client] Sector " + sec.sector_id + " started.");
                    rebuildList(true);
                }, function(err) {
                    print("[multi-sector-sim:client] Error starting sector " + sec.sector_id + ": " + err);
                    notifyUser("[scarlet]Ошибка запуска сектора " + sec.sector_id + ": " + err + "[]", null);
                    rebuildList(true);
                });
            }).size(130, 42).tooltip("Запустить честную симуляцию в фоне");
        }

        listTable.add(secTable).growX().pad(4).row();
    });
}

function syncUniverseSectors(sectors) {
    if (!sectors || !Array.isArray(sectors)) return;
    try {
        var curSecId = (Vars.state && Vars.state.isGame() && Vars.state.rules && Vars.state.rules.sector) ? Vars.state.rules.sector.id : null;
        sectors.forEach(function(sec) {
            if (!sec.resources || Object.keys(sec.resources).length === 0) return;
            if (curSecId !== null && sec.sector_id === curSecId) return;

            var planet = null;
            try {
                planet = Vars.content.planet(sec.planet) || Vars.content.planets().find(function(p) { return String(p.name).toLowerCase() === String(sec.planet).toLowerCase(); });
            } catch (ep) {}
            if (planet && planet.sectors && planet.sectors.size > sec.sector_id) {
                var secObj = planet.sectors.get(sec.sector_id);
                if (secObj && secObj.info && secObj.info.items) {
                    for (var rk in sec.resources) {
                        if (sec.resources.hasOwnProperty(rk)) {
                            var amt = sec.resources[rk];
                            var it = null;
                            try {
                                it = Vars.content.item(rk) || Vars.content.items().find(function(i) { return String(i.name) === String(rk); });
                            } catch (ei) {}
                            if (it && it.name) {
                                if (amt > secObj.info.items.get(it)) {
                                    secObj.info.items.set(it, amt);
                                }
                            }
                        }
                    }
                }
            }
        });
    } catch (e) {
        print("[multi-sector-sim:client] syncUniverseSectors error: " + e);
    }
}

function rebuildList(force) {
    if (listLoading && !force) return;
    listLoading = true;

    ensureSimDialog();
    if (!listTable.hasChildren()) {
        listTable.clear();
        listTable.add("[gray]Загрузка списка секторов...[]").row();
    }

    print("[multi-sector-sim:client] Fetching sectors list from orchestrator...");
    fetchJson("/api/sectors", "GET", null, function(data) {
        listLoading = false;
        Core.app.post(run(function() {
            var sectors = data.sectors || [];
            syncUniverseSectors(sectors);
            renderSectors(sectors);
        }));
    }, function(err) {
        listLoading = false;
        Core.app.post(run(function() {
            if (!listTable) return;
            listTable.clear();
            listTable.add("[scarlet]Оркестратор не запущен (127.0.0.1:8765)[]").padBottom(10).row();
            listTable.button("[accent]▶ Запустить оркестратор сейчас (1 клик)[]", function() {
                listTable.clear();
                listTable.add("[golden]Оркестратор запускается... подождите 2 сек[]").row();
                launchOrchestrator();
                Timer.schedule(run(function() {
                    rebuildList(true);
                }), 2.5);
            }).size(320, 54).padBottom(12).row();
            listTable.add("[lightgray]Или запустите start_sim.bat в папке c:\\antig[]").row();
        }));
    });
}

function showSimDialog() {
    print("[multi-sector-sim:client] Opening simDialog...");
    ensureSimDialog();
    rebuildList(false);
    simDialog.show();
    startAutoRefresh();
}

function toggleSimDialog() {
    ensureSimDialog();
    if (simDialog.isShown()) {
        print("[multi-sector-sim:client] Hiding simDialog...");
        simDialog.hide();
        stopAutoRefresh();
    } else {
        print("[multi-sector-sim:client] Showing simDialog...");
        rebuildList(false);
        simDialog.show();
        startAutoRefresh();
    }
}

if (!Vars.headless) {
    Events.on(ClientLoadEvent, function() {
        print("[multi-sector-sim:client] Initializing multi-sector-sim client mod...");

        // 1. Hotkey F8: Toggle dialog anytime from anywhere
        Events.run(Trigger.update, function() {
            if (Core.input && Core.input.keyTap(KeyCode.f8)) {
                print("[multi-sector-sim:client] F8 key tapped.");
                toggleSimDialog();
            }
        });

        // 2. Continuous Sector Transition Tracker (Auto-Suspend on Enter, Auto-Resume on Leave, In-Memory Core Sync)
        var lastPlayerSectorId = null;

        function applyResourcesToActiveSector(secId, resources) {
            if (!resources || Object.keys(resources).length === 0) return;
            try {
                var curSecId = (Vars.state && Vars.state.isGame() && Vars.state.rules && Vars.state.rules.sector) ? Vars.state.rules.sector.id : null;
                if (curSecId !== secId) return;

                var core = null;
                if (Vars.player && Vars.player.team()) {
                    core = Vars.player.team().core();
                }
                if (!core && Vars.state && Vars.state.rules && Vars.state.rules.defaultTeam) {
                    core = Vars.state.rules.defaultTeam.core();
                }

                var updatedCount = 0;
                if (core && core.items) {
                    for (var itName in resources) {
                        if (!resources.hasOwnProperty(itName)) continue;
                        var it = Vars.content.item(itName) || Vars.content.items().find(function(i) { return String(i.name) === String(itName); });
                        if (it) {
                            var targetAmt = resources[itName];
                            var curAmt = core.items.get(it);
                            if (targetAmt > curAmt) {
                                core.items.set(it, targetAmt);
                                updatedCount++;
                            }
                        }
                    }
                }

                if (Vars.state.rules.sector && Vars.state.rules.sector.info && Vars.state.rules.sector.info.items) {
                    for (var itName in resources) {
                        if (!resources.hasOwnProperty(itName)) continue;
                        var it = Vars.content.item(itName) || Vars.content.items().find(function(i) { return String(i.name) === String(itName); });
                        if (it) {
                            var targetAmt = resources[itName];
                            var curAmt = Vars.state.rules.sector.info.items.get(it);
                            if (targetAmt > curAmt) {
                                Vars.state.rules.sector.info.items.set(it, targetAmt);
                            }
                        }
                    }
                }

                if (updatedCount > 0) {
                    print("[multi-sector-sim:client] Injected " + updatedCount + " simulated resource types into active core for sector " + secId);
                    notifyUser("[green]⚡ Фоновая симуляция: сектор " + secId + " синхронизирован! Ресурсы обновлены в ядре.[]", null);
                }
            } catch (e) {
                print("[multi-sector-sim:client] Error applying resources to active sector: " + e);
            }
        }

        function checkSectorTransition() {
            var currentSec = null;
            try {
                if (Vars.state && Vars.state.isGame() && Vars.state.rules && Vars.state.rules.sector) {
                    currentSec = Vars.state.rules.sector.id;
                }
            } catch (e) {}

            if (currentSec !== lastPlayerSectorId) {
                var prevSec = lastPlayerSectorId;
                lastPlayerSectorId = currentSec;

                if (prevSec !== null && prevSec !== undefined) {
                    print("[multi-sector-sim:client] Player left sector " + prevSec + ", requesting auto-resume...");
                    Timer.schedule(run(function() {
                        fetchJson("/api/player/leave", "POST", { sector_id: prevSec }, function(res) {
                            print("[multi-sector-sim:client] Auto-resume result: " + JSON.stringify(res));
                        });
                    }), 0.5);
                }

                if (currentSec !== null && currentSec !== undefined) {
                    print("[multi-sector-sim:client] Player entered sector " + currentSec + ", requesting auto-suspend & sync...");
                    fetchJson("/api/player/enter", "POST", { sector_id: currentSec }, function(res) {
                        print("[multi-sector-sim:client] Auto-suspend result: " + JSON.stringify(res));
                        if (res && res.resources) {
                            applyResourcesToActiveSector(currentSec, res.resources);
                        }
                    });
                }
            }
        }

        try {
            var worldLoadEventClass = null;
            try {
                worldLoadEventClass = EventType.WorldLoadEvent;
            } catch (e1) {
                try {
                    worldLoadEventClass = Packages.mindustry.game.EventType.WorldLoadEvent;
                } catch (e2) {}
            }
            if (worldLoadEventClass) {
                Events.on(worldLoadEventClass, function() {
                    checkSectorTransition();
                });
            }
        } catch (e) {
            print("[multi-sector-sim:client] WorldLoadEvent hook error: " + e);
        }

        Timer.schedule(run(function() {
            checkSectorTransition();
        }), 1.0, 0.5);

        // 3. Main Menu: Add button to menufrag & rebuild menuGroup
        try {
            Vars.ui.menufrag.addButton("Фон-Сектора", Icon.host, run(function() { showSimDialog(); }));
            if (Vars.ui.menuGroup) {
                Vars.ui.menuGroup.clear();
                Vars.ui.menufrag.build(Vars.ui.menuGroup);
            }
        } catch (e) {
            print("[multi-sector-sim:client] menufrag error: " + e);
        }

        // 4. Main Menu Top Overlay Button (extra guarantee)
        try {
            var menuTable = new Table();
            menuTable.setFillParent(true);
            menuTable.top().left().margin(10);
            menuTable.button("Фон-Сектора (F8)", Icon.host, function() {
                showSimDialog();
            }).size(180, 50).pad(4);
            Vars.ui.menuGroup.addChild(menuTable);
        } catch (e) {
            print("[multi-sector-sim:client] menuGroup error: " + e);
        }

        // 5. Planet Map Dialog (Campaign map)
        try {
            if (Vars.ui.planet && Vars.ui.planet.buttons) {
                Vars.ui.planet.buttons.button("Фон-Сектора", Icon.host, function() {
                    showSimDialog();
                }).size(180, 54).pad(4);
            }
        } catch (e) {
            print("[multi-sector-sim:client] planet error: " + e);
        }

        // 6. In-Game HUD
        try {
            var hudTable = new Table();
            hudTable.setFillParent(true);
            hudTable.top().right().margin(10);
            hudTable.button("Фон-Сектора (F8)", Icon.host, function() {
                showSimDialog();
            }).size(160, 48).name("multisector-button");

            Vars.ui.hudGroup.addChild(hudTable);
        } catch (e) {
            print("[multi-sector-sim:client] hudGroup error: " + e);
        }

        // 7. Pause Menu
        try {
            if (Vars.ui.paused && Vars.ui.paused.buttons) {
                Vars.ui.paused.buttons.button("Фон-Сектора", Icon.host, function() {
                    showSimDialog();
                }).size(220, 54).pad(6).row();
            }
        } catch (e) {
            print("[multi-sector-sim:client] paused error: " + e);
        }

        // 8. Singleplayer / Campaign Client Chat event listener (/boost, /goal, /sim)
        try {
            Events.on(ClientChatEvent, function(e) {
                if (!e || !e.message) return;
                var text = String(e.message).trim();
                if (!text.startsWith("/")) return;

                var parts = text.substring(1).split(/\s+/);
                var cmd = parts[0].toLowerCase();
                var args = parts.slice(1);

                if (cmd === "sim" || cmd === "boost" || cmd === "goal") {
                    handleSimCommand(cmd, args, null);
                }
            });
        } catch (e) {
            print("[multi-sector-sim:client] ClientChatEvent error: " + e);
        }

        // 9. Multiplayer Server Command runner (/boost, /goal, /sim)
        try {
            if (Vars.netServer && Vars.netServer.clientCommands) {
                Vars.netServer.clientCommands.register("sim", "[action]", "Управление фоновой симуляцией секторов", new Packages.arc.util.CommandHandler.CommandRunner({
                    accept: function(args, player) {
                        handleSimCommand("sim", args, player);
                    }
                }));

                Vars.netServer.clientCommands.register("boost", "<speed> [sector]", "Ускорить сектор (например: /boost 5 или /boost 10 170)", new Packages.arc.util.CommandHandler.CommandRunner({
                    accept: function(args, player) {
                        handleSimCommand("boost", args, player);
                    }
                }));

                Vars.netServer.clientCommands.register("goal", "<wave> [sector]", "Установить цель волны (например: /goal 50 или /goal 100 170)", new Packages.arc.util.CommandHandler.CommandRunner({
                    accept: function(args, player) {
                        handleSimCommand("goal", args, player);
                    }
                }));
            }
        } catch (e) {
            print("[multi-sector-sim:client] register commands error: " + e);
        }

        // 10. Check orchestrator connectivity once after launch (without loop spamming)
        Timer.schedule(run(function() {
            fetchJson("/api/status", "GET", null, function(data) {
                print("[multi-sector-sim:client] Orchestrator connected: " + JSON.stringify(data));
            }, function(err) {
                print("[multi-sector-sim:client] Orchestrator offline, attempting background startup...");
                launchOrchestrator();
            });
        }), 2.0);

        // 11. Ensure player name is populated from settings if available
        try {
            var savedName = String(Core.settings.getString("name", "")).trim();
            if (savedName && Vars.player && (!Vars.player.name || Vars.player.name.trim().isEmpty())) {
                Vars.player.name = savedName;
            }
        } catch (e) {
            print("[multi-sector-sim:client] player name check error: " + e);
        }
    });
}
