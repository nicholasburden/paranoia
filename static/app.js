(function () {
    "use strict";

    // --- State ---
    let ws = null;
    let playerId = sessionStorage.getItem("paranoia_player_id") || "";
    let roomCode = sessionStorage.getItem("paranoia_room_code") || "";
    let isHost = false;
    let currentPhase = "lobby";
    let players = [];
    let timerInterval = null;
    let reconnectTimeout = null;

    // --- DOM refs ---
    const $ = (sel) => document.querySelector(sel);
    const screens = {
        home: $("#screen-home"),
        lobby: $("#screen-lobby"),
        game: $("#screen-game"),
        gameover: $("#screen-gameover"),
    };

    // Home
    const inputName = $("#input-name");
    const inputCode = $("#input-code");
    const btnCreate = $("#btn-create");
    const btnJoin = $("#btn-join");
    const homeError = $("#home-error");

    // Lobby
    const roomCodeDisplay = $("#room-code-display");
    const playerList = $("#player-list");
    const lobbyStatus = $("#lobby-status");
    const btnStart = $("#btn-start");

    // Game
    const roundInfo = $("#round-info");
    const timerDisplay = $("#timer-display");
    const gameStatus = $("#game-status");
    const panelAsk = $("#panel-ask");
    const askTarget = $("#ask-target");
    const suggestionsDiv = $("#suggestions");
    const inputQuestion = $("#input-question");
    const btnSubmitQuestion = $("#btn-submit-question");
    const panelAnswer = $("#panel-answer");
    const answerQuestion = $("#answer-question");
    const answerButtons = $("#answer-buttons");
    const panelReveal = $("#panel-reveal");
    const revealMessage = $("#reveal-message");
    const btnDrink = $("#btn-drink");
    const btnPass = $("#btn-pass");
    const panelResult = $("#panel-result");
    const resultContent = $("#result-content");
    const btnEndGame = $("#btn-end-game");

    // Game over
    const recapList = $("#recap-list");
    const btnLobby = $("#btn-lobby");
    const btnHome = $("#btn-home");

    // --- Screen management ---
    function showScreen(name) {
        Object.values(screens).forEach((s) => s.classList.remove("active"));
        screens[name].classList.add("active");
    }

    // --- Timer ---
    function startTimer(seconds) {
        clearInterval(timerInterval);
        let remaining = seconds;
        timerDisplay.textContent = remaining + "s";
        timerDisplay.classList.remove("warning");

        timerInterval = setInterval(() => {
            remaining--;
            if (remaining <= 0) {
                clearInterval(timerInterval);
                timerDisplay.textContent = "0s";
                return;
            }
            timerDisplay.textContent = remaining + "s";
            if (remaining <= 5) {
                timerDisplay.classList.add("warning");
            }
        }, 1000);
    }

    function clearTimer() {
        clearInterval(timerInterval);
        timerDisplay.textContent = "";
        timerDisplay.classList.remove("warning");
    }

    // --- API helpers ---
    async function apiPost(url, body) {
        const res = await fetch(url, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body),
        });
        const data = await res.json();
        if (!res.ok) {
            throw new Error(data.error || "Request failed");
        }
        return data;
    }

    // --- WebSocket ---
    function connectWs() {
        if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) {
            return;
        }

        const proto = location.protocol === "https:" ? "wss:" : "ws:";
        const url = `${proto}//${location.host}/ws/${roomCode}/${playerId}`;
        ws = new WebSocket(url);

        ws.onopen = () => {
            clearTimeout(reconnectTimeout);
        };

        ws.onmessage = (evt) => {
            const msg = JSON.parse(evt.data);
            handleServerMessage(msg);
        };

        ws.onclose = (evt) => {
            if (evt.code === 4001) {
                // Server rejected: invalid room or player
                playerId = "";
                roomCode = "";
                isHost = false;
                sessionStorage.removeItem("paranoia_player_id");
                sessionStorage.removeItem("paranoia_room_code");
                showScreen("home");
                homeError.textContent = "Session expired. Please create or join a game.";
                return;
            }
            scheduleReconnect();
        };

        ws.onerror = () => {
            // onclose will fire after this
        };
    }

    function scheduleReconnect() {
        if (!roomCode || !playerId) return;
        clearTimeout(reconnectTimeout);
        reconnectTimeout = setTimeout(() => {
            connectWs();
        }, 2000);
    }

    function send(msg) {
        if (ws && ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify(msg));
        }
    }

    // --- Visibility-based reconnect ---
    document.addEventListener("visibilitychange", () => {
        if (!document.hidden && roomCode && playerId) {
            if (!ws || ws.readyState !== WebSocket.OPEN) {
                connectWs();
            }
        }
    });

    // --- Hide all game panels ---
    function hideAllPanels() {
        panelAsk.style.display = "none";
        panelAnswer.style.display = "none";
        panelReveal.style.display = "none";
        panelResult.style.display = "none";
    }

    // --- Message handler ---
    function handleServerMessage(msg) {
        switch (msg.type) {
            case "player_list":
                updatePlayerList(msg.players);
                break;
            case "phase_change":
                handlePhaseChange(msg);
                break;
            case "prompt_question":
                showAskPanel(msg);
                break;
            case "prompt_answer":
                showAnswerPanel(msg);
                break;
            case "answer_given":
                handleAnswerGiven(msg);
                break;
            case "prompt_reveal":
                showRevealPanel(msg);
                break;
            case "turn_result":
                showResult(msg);
                break;
            case "sync":
                handleSync(msg);
                break;
            case "error":
                showError(msg.message);
                break;
        }
    }

    function showError(message) {
        // Show error in whatever screen is active
        if (screens.home.classList.contains("active")) {
            homeError.textContent = message;
        } else {
            gameStatus.textContent = message;
        }
    }

    // --- Player list ---
    function updatePlayerList(list) {
        players = list;
        // Lobby player list
        playerList.innerHTML = "";
        list.forEach((p) => {
            const li = document.createElement("li");
            if (!p.connected) li.classList.add("disconnected");
            let html = `<span>${escHtml(p.name)}</span>`;
            if (p.is_host) {
                html += `<span class="host-badge">Host</span>`;
            }
            li.innerHTML = html;
            playerList.appendChild(li);
        });

        // Update lobby status
        const count = list.length;
        if (count < 3) {
            lobbyStatus.textContent = `Need at least 3 players (${count}/3)`;
            btnStart.disabled = true;
        } else {
            lobbyStatus.textContent = `${count} players ready`;
            btnStart.disabled = false;
        }
    }

    // --- Phase changes ---
    function handlePhaseChange(msg) {
        currentPhase = msg.phase;
        hideAllPanels();
        clearTimer();

        if (msg.phase === "lobby") {
            showScreen("lobby");
            return;
        }

        if (msg.phase === "game_over") {
            showGameOver(msg.recap || []);
            return;
        }

        showScreen("game");
        btnEndGame.style.display = isHost ? "inline-block" : "none";

        if (msg.round !== undefined) {
            roundInfo.textContent = `Round ${msg.round} \u2022 Turn ${msg.turn}/${msg.total_turns}`;
        }

        if (msg.phase === "writing_question") {
            if (msg.asker_id === playerId) {
                gameStatus.textContent = "Write a question...";
            } else if (msg.answerer_id === playerId) {
                gameStatus.textContent = `${msg.asker_name} is writing a question for you...`;
            } else {
                gameStatus.textContent = `${msg.asker_name} is writing a question for ${msg.answerer_name}...`;
            }
            if (msg.timeout) startTimer(msg.timeout);
        } else if (msg.phase === "answering") {
            if (msg.answerer_id === playerId) {
                gameStatus.textContent = "Answer the question...";
            } else {
                gameStatus.textContent = `${msg.answerer_name} is answering...`;
            }
            if (msg.timeout) startTimer(msg.timeout);
        } else if (msg.phase === "reveal_choice") {
            if (msg.named_player_id === playerId) {
                gameStatus.textContent = "Your name was said! Drink to reveal the question?";
            } else {
                gameStatus.textContent = `Waiting for ${msg.named_player_name} to decide...`;
            }
            if (msg.timeout) startTimer(msg.timeout);
        } else if (msg.phase === "showing_result") {
            gameStatus.textContent = "";
        }
    }

    // --- Ask panel ---
    function showAskPanel(msg) {
        hideAllPanels();
        panelAsk.style.display = "block";
        askTarget.textContent = msg.answerer_name;
        inputQuestion.value = "";

        suggestionsDiv.innerHTML = "";
        (msg.suggestions || []).forEach((s) => {
            const btn = document.createElement("button");
            btn.className = "suggestion-btn";
            btn.textContent = s;
            btn.onclick = () => {
                inputQuestion.value = s;
                inputQuestion.focus();
            };
            suggestionsDiv.appendChild(btn);
        });

        if (msg.timeout) startTimer(msg.timeout);
    }

    // --- Answer panel ---
    function showAnswerPanel(msg) {
        hideAllPanels();
        panelAnswer.style.display = "block";
        answerQuestion.textContent = msg.question;

        answerButtons.innerHTML = "";
        (msg.players || []).forEach((p) => {
            const btn = document.createElement("button");
            btn.className = "player-btn";
            btn.textContent = p.name;
            btn.onclick = () => {
                send({ type: "submit_answer", answer_player_id: p.id });
                panelAnswer.style.display = "none";
                gameStatus.textContent = "Answer submitted!";
            };
            answerButtons.appendChild(btn);
        });

        if (msg.timeout) startTimer(msg.timeout);
    }

    // --- Answer given ---
    function handleAnswerGiven(msg) {
        hideAllPanels();
        let text = `${msg.answerer_name} answered: ${msg.named_player_name}`;
        if (msg.timed_out) text += " (timed out - random pick)";
        gameStatus.textContent = text;
    }

    // --- Reveal panel ---
    function showRevealPanel(msg) {
        hideAllPanels();
        panelReveal.style.display = "block";
        revealMessage.textContent = msg.message;
        if (msg.timeout) startTimer(msg.timeout);
    }

    // --- Turn result ---
    function showResult(msg) {
        hideAllPanels();
        clearTimer();

        if (msg.skipped) {
            gameStatus.textContent = msg.reason || "Turn skipped";
            return;
        }

        panelResult.style.display = "block";
        gameStatus.textContent = "";

        let html = "";
        if (msg.drink) {
            html += `<div class="result-action drink">${escHtml(msg.named_player_name)} drank!</div>`;
            html += `<div class="result-detail">${escHtml(msg.answerer_name)} was asked about them</div>`;
            html += `<div class="result-question">"${escHtml(msg.question)}"</div>`;
        } else {
            html += `<div class="result-action pass">${escHtml(msg.named_player_name)} passed</div>`;
            html += `<div class="result-detail">The question stays a mystery...</div>`;
            if (msg.timed_out) {
                html += `<div class="result-detail">(timed out)</div>`;
            }
        }
        resultContent.innerHTML = html;
    }

    // --- Game over ---
    function showGameOver(recap) {
        showScreen("gameover");
        clearTimer();
        recapList.innerHTML = "";

        if (recap.length === 0) {
            recapList.innerHTML = '<p style="color:var(--text-dim)">No turns were played.</p>';
            return;
        }

        recap.forEach((item) => {
            const div = document.createElement("div");
            div.className = "recap-item";

            let html = `<div class="recap-who">${escHtml(item.asker_name)} asked about ${escHtml(item.answerer_name)}</div>`;
            html += `<div class="recap-answer">Answer: ${escHtml(item.named_player_name)}</div>`;

            if (item.revealed && item.question) {
                html += `<div class="recap-question">"${escHtml(item.question)}"</div>`;
            } else {
                html += `<div class="recap-hidden">Question not revealed</div>`;
            }

            div.innerHTML = html;
            recapList.appendChild(div);
        });
    }

    // --- Sync (reconnect) ---
    function handleSync(msg) {
        playerId = msg.player_id;
        isHost = msg.is_host;
        roomCode = msg.room_code;

        if (msg.players) {
            updatePlayerList(msg.players);
        }

        if (msg.phase === "lobby") {
            showScreen("lobby");
            roomCodeDisplay.textContent = roomCode;
            btnStart.style.display = isHost ? "block" : "none";
            return;
        }

        if (msg.phase === "game_over") {
            showGameOver(msg.recap || []);
            return;
        }

        // In-game sync
        showScreen("game");
        btnEndGame.style.display = isHost ? "inline-block" : "none";
        hideAllPanels();

        if (msg.turn_info) {
            const ti = msg.turn_info;
            roundInfo.textContent = `Round ${ti.round} \u2022 Turn ${ti.turn}/${ti.total_turns}`;

            if (msg.phase === "writing_question") {
                if (ti.asker_id === playerId && msg.prompt_question) {
                    gameStatus.textContent = "Write a question...";
                    showAskPanel(msg.prompt_question);
                } else if (ti.answerer_id === playerId) {
                    gameStatus.textContent = `${ti.asker_name} is writing a question for you...`;
                } else {
                    gameStatus.textContent = `${ti.asker_name} is writing a question for ${ti.answerer_name}...`;
                }
            } else if (msg.phase === "answering") {
                if (ti.answerer_id === playerId && msg.prompt_answer) {
                    gameStatus.textContent = "Answer the question...";
                    showAnswerPanel(msg.prompt_answer);
                } else {
                    const answererName = ti.answerer_name;
                    gameStatus.textContent = `${answererName} is answering...`;
                }
            } else if (msg.phase === "reveal_choice") {
                if (ti.named_player_id === playerId) {
                    gameStatus.textContent = "Your name was said! Drink to reveal the question?";
                    panelReveal.style.display = "block";
                    revealMessage.textContent = "You were named! Drink to reveal, or pass.";
                } else {
                    gameStatus.textContent = `Waiting for ${ti.named_player_name} to decide...`;
                }
            } else if (msg.phase === "showing_result") {
                gameStatus.textContent = "Showing result...";
            }
        }
    }

    // --- Utility ---
    function escHtml(str) {
        const d = document.createElement("div");
        d.textContent = str || "";
        return d.innerHTML;
    }

    // --- Event listeners ---
    btnCreate.addEventListener("click", async () => {
        const name = inputName.value.trim();
        if (!name) {
            homeError.textContent = "Enter your name";
            return;
        }
        homeError.textContent = "";
        btnCreate.disabled = true;
        try {
            const data = await apiPost("/api/create", { name });
            playerId = data.player_id;
            roomCode = data.room_code;
            isHost = true;
            sessionStorage.setItem("paranoia_player_id", playerId);
            sessionStorage.setItem("paranoia_room_code", roomCode);

            roomCodeDisplay.textContent = roomCode;
            btnStart.style.display = "block";
            showScreen("lobby");
            connectWs();
        } catch (e) {
            homeError.textContent = e.message;
        } finally {
            btnCreate.disabled = false;
        }
    });

    btnJoin.addEventListener("click", async () => {
        const name = inputName.value.trim();
        const code = inputCode.value.trim().toUpperCase();
        if (!name) {
            homeError.textContent = "Enter your name";
            return;
        }
        if (!code || code.length !== 4) {
            homeError.textContent = "Enter a 4-letter room code";
            return;
        }
        homeError.textContent = "";
        btnJoin.disabled = true;
        try {
            // Use rejoin endpoint — handles both new joins and reconnects
            const data = await apiPost("/api/rejoin", { name, room_code: code });
            playerId = data.player_id;
            roomCode = data.room_code;
            isHost = false;
            sessionStorage.setItem("paranoia_player_id", playerId);
            sessionStorage.setItem("paranoia_room_code", roomCode);

            roomCodeDisplay.textContent = roomCode;
            btnStart.style.display = "none";
            showScreen("lobby");
            connectWs();
        } catch (e) {
            homeError.textContent = e.message;
        } finally {
            btnJoin.disabled = false;
        }
    });

    btnStart.addEventListener("click", () => {
        send({ type: "start_game" });
    });

    btnSubmitQuestion.addEventListener("click", () => {
        const q = inputQuestion.value.trim();
        if (!q) return;
        send({ type: "submit_question", question: q });
        panelAsk.style.display = "none";
        gameStatus.textContent = "Question submitted! Waiting for answer...";
    });

    btnDrink.addEventListener("click", () => {
        send({ type: "reveal_choice", drink: true });
        panelReveal.style.display = "none";
        gameStatus.textContent = "You chose to drink!";
    });

    btnPass.addEventListener("click", () => {
        send({ type: "reveal_choice", drink: false });
        panelReveal.style.display = "none";
        gameStatus.textContent = "You passed.";
    });

    btnEndGame.addEventListener("click", () => {
        send({ type: "end_game" });
    });

    btnLobby.addEventListener("click", () => {
        send({ type: "return_to_lobby" });
    });

    btnHome.addEventListener("click", () => {
        if (ws) ws.close();
        ws = null;
        playerId = "";
        roomCode = "";
        isHost = false;
        sessionStorage.removeItem("paranoia_player_id");
        sessionStorage.removeItem("paranoia_room_code");
        showScreen("home");
    });

    // Room code copy on tap
    roomCodeDisplay.addEventListener("click", () => {
        if (navigator.clipboard) {
            navigator.clipboard.writeText(roomCodeDisplay.textContent);
        }
    });

    // Enter key support
    inputName.addEventListener("keydown", (e) => {
        if (e.key === "Enter") {
            if (inputCode.value.trim()) btnJoin.click();
            else btnCreate.click();
        }
    });
    inputCode.addEventListener("keydown", (e) => {
        if (e.key === "Enter") btnJoin.click();
    });
    inputQuestion.addEventListener("keydown", (e) => {
        if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();
            btnSubmitQuestion.click();
        }
    });

    // --- Init ---
    // If we have stored credentials, try to reconnect directly
    if (playerId && roomCode) {
        showScreen("lobby");
        roomCodeDisplay.textContent = roomCode;
        connectWs();
    } else {
        showScreen("home");
    }
})();
