from __future__ import annotations

HTML_SCAFFOLD = """<!doctype html>
<html>
  <head>
    <meta charset="utf-8" />
    <title>Travel Planner Prototype</title>
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <style>
      :root {
        --bg: #f5f5f2;
        --panel: #ffffff;
        --ink: #1f2937;
        --accent: #0f766e;
        --border: #d1d5db;
      }
      body {
        margin: 0;
        font-family: "Segoe UI", Tahoma, sans-serif;
        color: var(--ink);
        background: linear-gradient(160deg, #f5f5f2, #e2e8f0);
      }
      .wrap {
        max-width: 1200px;
        margin: 24px auto;
        padding: 16px;
        display: grid;
        grid-template-columns: minmax(0, 2fr) minmax(280px, 1fr);
        gap: 16px;
      }
      .panel {
        background: var(--panel);
        border: 1px solid var(--border);
        border-radius: 12px;
        padding: 16px;
        box-shadow: 0 8px 30px rgba(31, 41, 55, 0.08);
      }
      h1 {
        margin: 0 0 12px 0;
        font-size: 1.5rem;
      }
      p {
        margin-top: 0;
      }
      textarea {
        width: 100%;
        min-height: 90px;
        border: 1px solid var(--border);
        border-radius: 8px;
        padding: 10px;
        font-size: 0.95rem;
        box-sizing: border-box;
      }
      .row {
        margin-top: 12px;
        display: flex;
        gap: 10px;
        align-items: center;
      }
      button {
        background: var(--accent);
        border: none;
        color: white;
        padding: 10px 14px;
        border-radius: 8px;
        font-weight: 600;
        cursor: pointer;
      }
      button:disabled {
        opacity: 0.6;
        cursor: not-allowed;
      }
      pre {
        margin-top: 14px;
        padding: 12px;
        border-radius: 8px;
        background: #0b1020;
        color: #e5e7eb;
        overflow: auto;
        max-height: 60vh;
      }
      input[type=text] {
        border: 1px solid var(--border);
        border-radius: 8px;
        padding: 8px;
      }
      select {
        border: 1px solid var(--border);
        border-radius: 8px;
        padding: 8px;
      }
      #breadcrumb {
        margin: 0 0 10px 0;
        padding: 8px 10px;
        border-radius: 8px;
        background: #ecfeff;
        border: 1px solid #a5f3fc;
        font-size: 0.9rem;
      }
      .sidepanel {
        background: var(--panel);
        border: 1px solid var(--border);
        border-radius: 12px;
        padding: 16px;
        box-shadow: 0 8px 30px rgba(31, 41, 55, 0.08);
        position: sticky;
        top: 16px;
        height: fit-content;
      }
      #activityFeed {
        max-height: 78vh;
        min-height: 280px;
      }
      #advisorChat {
        max-height: 220px;
      }
      #advisorSuggestion {
        max-height: 260px;
      }
      @media (max-width: 980px) {
        .wrap {
          grid-template-columns: 1fr;
        }
        .sidepanel {
          position: static;
        }
        #activityFeed {
          max-height: 45vh;
        }
      }
    </style>
  </head>
  <body>
    <div class="wrap">
      <div class="panel">
        <h1>Travel Planner Prototype</h1>
        <div id="breadcrumb">Stage path: criteria_gathering</div>
        <p>Enter a travel prompt and run criteria + inspiration stages.</p>
        <div class="row">
          <label><input id="replayToggle" type="checkbox" /> Replay (use cached LLM response)</label>
          <span id="replayInfo"></span>
        </div>
        <textarea id="input">I want to go somewhere warm in March under $3000</textarea>
        <div class="row">
          <button id="runBtn">Plan Trip</button>
          <span id="status"></span>
        </div>
        <h3>Traveler Profile (editable JSON)</h3>
        <div class="row">
          <select id="profileSelect"></select>
          <input id="newProfileName" type="text" placeholder="New profile name" />
          <button id="createProfileBtn">Create Profile</button>
          <span id="profileStatus"></span>
        </div>
        <textarea id="travelerJson" rows="10">{}</textarea>
        <div class="row"><small>Traveler profile auto-saves after edits.</small></div>

        <h3>Active Itinerary</h3>
        <div class="row">
          <select id="itinerarySelect"></select>
          <button id="refreshItineraryBtn">Refresh</button>
        </div>
        <div class="row">
          <button id="generateMoreBtn">Generate More Options (+2)</button>
          <button id="deleteOptionBtn">Delete Selected</button>
          <button id="buildItineraryBtn">Build Chosen Itinerary</button>
          <button id="checkFitBtn">Check Itinerary Fit</button>
          <span id="buildHint"></span>
        </div>
        <pre id="activeItinerary">{}</pre>
        <h3>Itinerary Fit Check</h3>
        <pre id="fitReport">[]</pre>

        <h3>Help Me Choose (on-demand)</h3>
        <div class="row">
          <button id="compareBtn">Generate Help Me Choose</button>
        </div>
        <pre id="comparisonGuidance">{}</pre>

        <h3>Orchestration Assistant (Local SLM)</h3>
        <pre id="advisorSuggestion">{}</pre>
        <div class="row">
          <button id="advisorApproveBtn">Approve Suggested Action</button>
          <button id="advisorApproveAltBtn">Approve Alternative Action</button>
          <button id="advisorSuggestBtn">Ask Advisor</button>
        </div>
        <textarea id="advisorInput" rows="3" placeholder="Ask what to do next, or give an instruction."></textarea>
        <pre id="advisorChat">[]</pre>
        <h3>Action History</h3>
        <pre id="actionHistory">[]</pre>

        <h3>Workspace State</h3>
        <pre id="output">{}</pre>
      </div>
      <aside class="sidepanel">
        <h3>LLM Activity Feed</h3>
        <pre id="activityFeed"></pre>
      </aside>
    </div>
    <script>
      const runBtn = document.getElementById("runBtn");
      const createProfileBtn = document.getElementById("createProfileBtn");
      const generateMoreBtn = document.getElementById("generateMoreBtn");
      const deleteOptionBtn = document.getElementById("deleteOptionBtn");
      const buildItineraryBtn = document.getElementById("buildItineraryBtn");
      const checkFitBtn = document.getElementById("checkFitBtn");
      const buildHint = document.getElementById("buildHint");
      const compareBtn = document.getElementById("compareBtn");
      const advisorSuggestBtn = document.getElementById("advisorSuggestBtn");
      const advisorApproveBtn = document.getElementById("advisorApproveBtn");
      const advisorApproveAltBtn = document.getElementById("advisorApproveAltBtn");
      const advisorInput = document.getElementById("advisorInput");
      const advisorSuggestion = document.getElementById("advisorSuggestion");
      const advisorChat = document.getElementById("advisorChat");
      const actionHistory = document.getElementById("actionHistory");
      const refreshItineraryBtn = document.getElementById("refreshItineraryBtn");
      const input = document.getElementById("input");
      const replayToggle = document.getElementById("replayToggle");
      const replayInfo = document.getElementById("replayInfo");
      const profileSelect = document.getElementById("profileSelect");
      const newProfileName = document.getElementById("newProfileName");
      const profileStatus = document.getElementById("profileStatus");
      const travelerJson = document.getElementById("travelerJson");
      const itinerarySelect = document.getElementById("itinerarySelect");
      const activeItinerary = document.getElementById("activeItinerary");
      const fitReport = document.getElementById("fitReport");
      const comparisonGuidance = document.getElementById("comparisonGuidance");
      const activityFeed = document.getElementById("activityFeed");
      const breadcrumb = document.getElementById("breadcrumb");
      const output = document.getElementById("output");
      const status = document.getElementById("status");
      let hasUserEditedInput = false;
      let travelerDirty = false;
      let travelerSaveTimer = null;
      const buildDebug = (...args) => console.log("[ui-build]", ...args);

      async function postJson(path, payload) {
        console.log("[ui] POST", path, payload || {});
        const startedAt = Date.now();
        const res = await fetch(path, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload || {})
        });
        let data = {};
        try {
          data = await res.json();
        } catch (e) {
          throw new Error("Non-JSON response from backend for " + path);
        }
        console.log("[ui] POST response", path, res.status, data, "elapsed_ms=", Date.now() - startedAt);
        if (!res.ok) {
          throw new Error(data.error || "Request failed");
        }
        return data;
      }

      async function refreshState() {
        const res = await fetch("/state");
        const data = await res.json();
        output.textContent = JSON.stringify(data, null, 2);
        if (!travelerDirty) {
          travelerJson.value = JSON.stringify(data.traveler_profile || {}, null, 2);
        }
        activeItinerary.textContent = JSON.stringify(data.active_itinerary || {}, null, 2);
        fitReport.textContent = JSON.stringify(data.itinerary_fit_report || [], null, 2);
        actionHistory.textContent = JSON.stringify(data.action_history || [], null, 2);
        populateProfilesDropdown(data.traveler_profiles || [], data.active_traveler_profile_id);
        advisorSuggestion.textContent = JSON.stringify(data.advisor_suggestion || {}, null, 2);
        advisorChat.textContent = JSON.stringify(data.advisor_chat || [], null, 2);
        const alternatives = ((data.advisor_suggestion || {}).alternatives || []);
        advisorApproveAltBtn.disabled = alternatives.length === 0;
        const cg = data.comparison_guidance || {};
        if (cg.summary) {
          const header = "Status: " + (cg.status || "unknown") + " | Model: " + (cg.model || "n/a");
          comparisonGuidance.textContent = header + "\\n\\n" + cg.summary;
        } else if (cg.error) {
          comparisonGuidance.textContent = "Status: error\\n\\n" + cg.error;
        } else {
          comparisonGuidance.textContent = JSON.stringify(cg, null, 2);
        }
        const activity = data.llm_activity || [];
        activityFeed.textContent = activity.map(e => "(" + e.kind + ") " + e.message).join("\\n");
        activityFeed.scrollTop = activityFeed.scrollHeight;
        replayToggle.checked = !!data.replay_mode;
        const cache = data.replay_cache_available || {};
        const cachedPrompt = data.last_cached_prompt_input || "";
        if (cachedPrompt && (!hasUserEditedInput || !input.value.trim())) {
          input.value = cachedPrompt;
        }
        const lastCall = data.llm_last_call_info || {};
        const replayNotice = lastCall.message ? " | " + lastCall.message : "";
        replayInfo.textContent =
          "Cache criteria=" + !!cache.criteria_gathering +
          ", inspiration=" + !!cache.inspiration +
          ", inspiration_additional=" + !!cache.inspiration_additional +
          (cachedPrompt ? " | Last prompt: " + cachedPrompt : "") +
          replayNotice;
        const crumbs = (data.breadcrumb || []).map(x => {
          const s = x.status === "current" ? "[" + x.stage + "]" : x.stage;
          return x.status === "completed" ? "[done] " + s : s;
        });
        breadcrumb.textContent = "Stage path: " + crumbs.join(" > ");

        populateItineraryDropdown(data.itinerary_options || [], data.active_itinerary_id);
        const active = data.active_itinerary || {};
        const enrichStatusRaw = ((active.destination_enrichment || {}).status || "");
        const enrichStatus = String(enrichStatusRaw).trim().toLowerCase();
        const canBuild = enrichStatus === "completed" || enrichStatus === "error";
        buildItineraryBtn.disabled = !canBuild;
        if (enrichStatus === "error") {
          buildHint.textContent = "Ready to build (with enrichment warnings).";
        } else if (canBuild) {
          buildHint.textContent = "Ready to build.";
        } else {
          buildHint.textContent = "Waiting for enrichment: " + (enrichStatusRaw || "not_started");
        }
      }

      function populateItineraryDropdown(options, activeId) {
        itinerarySelect.innerHTML = "";
        for (const opt of options) {
          const node = document.createElement("option");
          node.value = opt.option_id;
          const destination = opt.chosen_destination || "TBD destination";
          const enrich = (opt.destination_enrichment && opt.destination_enrichment.status) || "not_started";
          node.textContent = opt.name + " | " + opt.status + " | " + destination + " | enrich:" + enrich;
          if (opt.option_id === activeId) {
            node.selected = true;
          }
          itinerarySelect.appendChild(node);
        }
      }

      function populateProfilesDropdown(profiles, activeId) {
        const current = profileSelect.value;
        profileSelect.innerHTML = "";
        for (const p of profiles) {
          const node = document.createElement("option");
          node.value = p.profile_id;
          node.textContent = p.profile_name + (p.is_default ? " (default)" : "");
          if (p.profile_id === activeId || (!activeId && p.profile_id === current)) {
            node.selected = true;
          }
          profileSelect.appendChild(node);
        }
      }

      async function runPlan() {
        runBtn.disabled = true;
        status.textContent = "Running...";
        try {
          await postJson("/plan", { input: input.value });
          await refreshState();
          status.textContent = "Done";
        } catch (err) {
          output.textContent = JSON.stringify({ error: String(err) }, null, 2);
          status.textContent = "Error";
        } finally {
          runBtn.disabled = false;
        }
      }

      async function setReplayMode() {
        try {
          await postJson("/replay", { enabled: replayToggle.checked });
          await refreshState();
          status.textContent = replayToggle.checked ? "Replay enabled" : "Replay disabled";
        } catch (err) {
          output.textContent = JSON.stringify({ error: String(err) }, null, 2);
          status.textContent = "Error";
        }
      }

      async function saveTravelerAuto() {
        status.textContent = "Auto-saving traveler profile...";
        try {
          const payload = JSON.parse(travelerJson.value || "{}");
          await postJson("/traveler", { traveler_profile: payload });
          travelerDirty = false;
          profileStatus.textContent = "Saved";
          setTimeout(() => { if (!travelerDirty) profileStatus.textContent = ""; }, 1200);
          status.textContent = "Traveler profile saved";
        } catch (err) {
          profileStatus.textContent = "Invalid JSON (not saved yet)";
          status.textContent = "Waiting for valid JSON...";
        }
      }

      function scheduleTravelerAutosave() {
        travelerDirty = true;
        profileStatus.textContent = "Editing...";
        if (travelerSaveTimer) {
          clearTimeout(travelerSaveTimer);
        }
        travelerSaveTimer = setTimeout(() => {
          saveTravelerAuto();
        }, 700);
      }

      async function createProfile() {
        createProfileBtn.disabled = true;
        status.textContent = "Creating profile...";
        try {
          await postJson("/profiles/create", { name: newProfileName.value || "New Profile" });
          travelerDirty = false;
          await refreshState();
          newProfileName.value = "";
          profileStatus.textContent = "Profile created";
          status.textContent = "Profile created";
        } catch (err) {
          output.textContent = JSON.stringify({ error: String(err) }, null, 2);
          status.textContent = "Error";
        } finally {
          createProfileBtn.disabled = false;
        }
      }

      async function switchProfile() {
        const profileId = profileSelect.value;
        if (!profileId) {
          return;
        }
        status.textContent = "Switching profile...";
        try {
          await postJson("/profiles/select", { profile_id: profileId });
          travelerDirty = false;
          await refreshState();
          profileStatus.textContent = "Switched";
          status.textContent = "Profile switched";
        } catch (err) {
          output.textContent = JSON.stringify({ error: String(err) }, null, 2);
          status.textContent = "Error";
        }
      }

      async function checkItineraryFit() {
        checkFitBtn.disabled = true;
        status.textContent = "Checking itinerary fit...";
        try {
          await postJson("/itineraries/check_fit", {});
          await refreshState();
          status.textContent = "Fit check completed";
        } catch (err) {
          output.textContent = JSON.stringify({ error: String(err) }, null, 2);
          status.textContent = "Error";
        } finally {
          checkFitBtn.disabled = false;
        }
      }

      async function generateMoreOptions() {
        generateMoreBtn.disabled = true;
        status.textContent = "Generating more options...";
        try {
          await postJson("/itineraries/generate_more", { count: 2 });
          await refreshState();
          status.textContent = "More options generated";
        } catch (err) {
          output.textContent = JSON.stringify({ error: String(err) }, null, 2);
          status.textContent = "Error";
        } finally {
          generateMoreBtn.disabled = false;
        }
      }

      async function selectActiveItinerary() {
        status.textContent = "Selecting itinerary...";
        try {
          const optionId = itinerarySelect.value;
          if (!optionId) {
            status.textContent = "No itinerary selected";
            return;
          }
          await postJson("/itineraries/select", { option_id: optionId });
          await refreshState();
          status.textContent = "Itinerary selected";
        } catch (err) {
          output.textContent = JSON.stringify({ error: String(err) }, null, 2);
          status.textContent = "Error";
        }
      }

      async function deleteSelectedOption() {
        deleteOptionBtn.disabled = true;
        status.textContent = "Deleting itinerary...";
        try {
          const optionId = itinerarySelect.value;
          if (!optionId) {
            status.textContent = "No itinerary selected";
            return;
          }
          await postJson("/itineraries/delete", { option_id: optionId });
          await refreshState();
          status.textContent = "Itinerary deleted";
        } catch (err) {
          output.textContent = JSON.stringify({ error: String(err) }, null, 2);
          status.textContent = "Error";
        } finally {
          deleteOptionBtn.disabled = false;
        }
      }

      async function generateComparison() {
        compareBtn.disabled = true;
        status.textContent = "Generating comparison...";
        try {
          await postJson("/compare", {});
          await refreshState();
          status.textContent = "Comparison updated";
        } catch (err) {
          output.textContent = JSON.stringify({ error: String(err) }, null, 2);
          status.textContent = "Error";
        } finally {
          compareBtn.disabled = false;
        }
      }

      async function askAdvisor() {
        advisorSuggestBtn.disabled = true;
        status.textContent = "Advisor thinking...";
        try {
          await postJson("/advisor/suggest", { message: advisorInput.value || "" });
          advisorInput.value = "";
          await refreshState();
          status.textContent = "Advisor updated";
        } catch (err) {
          output.textContent = JSON.stringify({ error: String(err) }, null, 2);
          status.textContent = "Error";
        } finally {
          advisorSuggestBtn.disabled = false;
        }
      }

      async function approveAdvisorAction() {
        advisorApproveBtn.disabled = true;
        status.textContent = "Executing advisor action...";
        try {
          await postJson("/advisor/approve", {});
          await refreshState();
          status.textContent = "Advisor action executed";
        } catch (err) {
          output.textContent = JSON.stringify({ error: String(err) }, null, 2);
          status.textContent = "Error";
        } finally {
          advisorApproveBtn.disabled = false;
        }
      }

      async function approveAdvisorAlternative() {
        advisorApproveAltBtn.disabled = true;
        status.textContent = "Executing advisor alternative...";
        try {
          await postJson("/advisor/approve_alternative", { index: 0 });
          await refreshState();
          status.textContent = "Advisor alternative executed";
        } catch (err) {
          output.textContent = JSON.stringify({ error: String(err) }, null, 2);
          status.textContent = "Error";
        } finally {
          // refreshState controls enabled-state based on current suggestion alternatives.
        }
      }

      async function buildChosenItinerary() {
        buildDebug("click", {
          disabled: buildItineraryBtn.disabled,
          selected_option_id: itinerarySelect.value || null,
          build_hint: buildHint.textContent || null
        });
        buildItineraryBtn.disabled = true;
        status.textContent = "Building itinerary...";
        output.textContent = JSON.stringify(
          { debug: "build_clicked", selected_option_id: itinerarySelect.value || null },
          null,
          2
        );
        let succeeded = false;
        try {
          buildDebug("sending POST /itineraries/build_active");
          await postJson("/itineraries/build_active", {});
          buildDebug("POST /itineraries/build_active succeeded");
          await refreshState();
          status.textContent = "Itinerary built";
          succeeded = true;
        } catch (err) {
          buildDebug("build error", String(err));
          output.textContent = JSON.stringify({ error: String(err) }, null, 2);
          status.textContent = "Error";
        } finally {
          if (!succeeded) {
            buildItineraryBtn.disabled = false;
            buildDebug("button re-enabled after failure");
          } else {
            buildDebug("build flow completed");
          }
        }
      }

      runBtn.addEventListener("click", runPlan);
      createProfileBtn.addEventListener("click", createProfile);
      generateMoreBtn.addEventListener("click", generateMoreOptions);
      deleteOptionBtn.addEventListener("click", deleteSelectedOption);
      buildItineraryBtn.addEventListener("click", buildChosenItinerary);
      buildItineraryBtn.onclick = buildChosenItinerary;
      checkFitBtn.addEventListener("click", checkItineraryFit);
      compareBtn.addEventListener("click", generateComparison);
      advisorSuggestBtn.addEventListener("click", askAdvisor);
      advisorApproveBtn.addEventListener("click", approveAdvisorAction);
      advisorApproveAltBtn.addEventListener("click", approveAdvisorAlternative);
      itinerarySelect.addEventListener("change", selectActiveItinerary);
      profileSelect.addEventListener("change", switchProfile);
      refreshItineraryBtn.addEventListener("click", refreshState);
      replayToggle.addEventListener("change", setReplayMode);
      input.addEventListener("input", () => {
        hasUserEditedInput = true;
      });
      travelerJson.addEventListener("input", scheduleTravelerAutosave);

      refreshState();
      setInterval(refreshState, 4000);
    </script>
  </body>
</html>
"""
