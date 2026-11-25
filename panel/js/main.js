/* AutoCutPR panel. Drives the Python engine and the ExtendScript host.
   Layout and token rules live in ../../DESIGN.md. */
(function () {
  var cs = new CSInterface();
  var nodeRequire = window.cep_node ? window.cep_node.require : null;
  var fs = nodeRequire ? nodeRequire("fs") : null;
  var path = nodeRequire ? nodeRequire("path") : null;
  var cp = nodeRequire ? nodeRequire("child_process") : null;

  var $ = function (id) { return document.getElementById(id); };
  var $$ = function (sel) { return Array.prototype.slice.call(document.querySelectorAll(sel)); };

  var config = { engineDir: "", cacheDir: "", python: "python" };
  var cuts = null;         // every candidate, in episode order
  var assets = {};         // logo measurement from the engine
  var speakers = null;     // diarization summary
  var effectName = null;   // Auto Reframe's real name, found by Diagnostics
  var STEPS = ["source", "find", "review", "build", "export", "trailer"];
  var reached = { source: true };

  /* ------------------------------------------------------------- basics */

  function setStatus(msg, cls) {
    $("status").textContent = msg || "";
    $("status").className = cls || "";
  }

  function log(line) {
    var el = $("log");
    el.textContent += line;
    el.scrollTop = el.scrollHeight;
  }

  function fmt(s) {
    var m = Math.floor(s / 60), sec = Math.floor(s % 60);
    return m + ":" + (sec < 10 ? "0" : "") + sec;
  }

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
    });
  }

  function loadConfig() {
    try {
      config = JSON.parse(fs.readFileSync(
        path.join(cs.getExtensionPath(), "config.json"), "utf8"));
    } catch (e) {
      setStatus("config.json missing or invalid: " + e, "err");
    }
  }

  /* --------------------------------------------------------- navigation */

  function go(step) {
    reached[step] = true;
    $$(".step").forEach(function (s) {
      s.classList.toggle("current", s.dataset.step === step);
    });
    $$(".rail-step").forEach(function (b) {
      var i = STEPS.indexOf(b.dataset.step);
      var here = STEPS.indexOf(step);
      b.classList.toggle("current", b.dataset.step === step);
      b.classList.toggle("done", i < here && reached[b.dataset.step]);
      b.disabled = !reached[b.dataset.step];
    });
    document.querySelector(".work").scrollTop = 0;
  }

  $$(".rail-step").forEach(function (b) {
    b.onclick = function () { if (!b.disabled) go(b.dataset.step); };
  });

  /* ------------------------------------------------------------- engine */

  function runEngine(args, onDone) {
    if (!cp) { setStatus("Node is not available in this panel", "err"); return null; }
    var proc = cp.spawn(config.python, [path.join(config.engineDir, "run.py")].concat(args),
      { windowsHide: true });
    proc.stdout.on("data", function (d) { log(d.toString("utf8")); });
    proc.stderr.on("data", function (d) { log(d.toString("utf8")); });
    proc.on("error", function (e) { setStatus("Could not start Python: " + e.message, "err"); });
    proc.on("close", function (code) { onDone(code); });
    return proc;
  }

  // `done` runs only on success; `always` runs either way, so a button that was
  // disabled for the call is never left stuck after an error.
  function callHost(fn, payload, busyMsg, done, always) {
    setStatus(busyMsg, "busy");
    cs.evalScript(fn + "(" + JSON.stringify(JSON.stringify(payload)) + ")", function (res) {
      try {
        var r = JSON.parse(res);
        if (r.error) setStatus(r.error, "err");
        else { setStatus(r.ok, "ok"); if (done) done(r); }
      } catch (e) {
        setStatus("Unexpected reply from Premiere: " + res, "err");
      }
      if (always) always();
    });
  }

  function media() { return $("mediaPath").value.trim(); }

  function outPath(name) { return path.join(config.cacheDir, name); }

  /* ------------------------------------------------------------- step 1 */

  $("btnSelected").onclick = function () {
    cs.evalScript("ACP_getSelection()", function (res) {
      try {
        var r = JSON.parse(res);
        if (r.error) return setStatus(r.error, "err");
        $("mediaPath").value = r.mediaPath;
        setStatus("Using " + r.name, "ok");
      } catch (e) { setStatus("Unexpected reply: " + res, "err"); }
    });
  };

  $("btnBrowse").onclick = function () {
    var r = window.cep.fs.showOpenDialog(false, false, "Pick the episode", null, null);
    if (r && r.data && r.data.length) $("mediaPath").value = r.data[0];
  };

  function mark(key, state, note) {
    var li = document.querySelector('.checklist li[data-key="' + key + '"]');
    if (!li) return;
    li.className = state;
    li.querySelector("em").textContent = note || "";
  }

  $("btnPrepare").onclick = function () {
    if (!media()) return setStatus("Pick the episode first", "err");
    $("btnPrepare").disabled = true;
    mark("transcript", "busy", "working");
    mark("waveform", "busy", "queued");
    mark("speakers", "busy", "queued");
    preparePass();
  };

  // Diarization is chunked and resumable, so a pass may end with work left.
  function preparePass() {
    setStatus("Preparing the episode. Speakers take the longest, it is cached after this.",
      "busy");
    runEngine(["--mode", "prepare", "--audio", media(),
               "--out", outPath("prepare.json")], function () {
      var st = {};
      try { st = JSON.parse(fs.readFileSync(outPath("prepare.json"), "utf8")); } catch (e) {}
      mark("transcript", st.transcript ? "ok" : "fail",
           st.transcript ? st.words + " words" : "failed");
      mark("waveform", st.waveform ? "ok" : "fail",
           st.waveform ? st.silences + " silences" : "failed");

      if (st.speakers_pending) {
        mark("speakers", "busy", st.speakers_pending + " chunks left");
        return preparePass();               // keep going where it left off
      }
      if (!st.speakers) {
        mark("speakers", "fail", "failed");
        $("btnPrepare").disabled = false;
        // Finished chunks are cached, so pressing Prepare again resumes
        // rather than starting over.
        return setStatus((st.error || "Speaker detection failed, see the log")
          + ". Finished work is kept; Prepare again resumes.", "err");
      }

      speakers = st.speakers;
      mark("speakers", "ok", speakers.speakers.length + " speakers");
      renderSpeakers();
      $("btnPrepare").disabled = false;
      setStatus("Episode ready", "ok");
      go("find");
    });
  }

  function renderSpeakers() {
    if (!speakers) return;
    $("speakerBox").hidden = false;
    $("speakers").innerHTML = speakers.speakers.map(function (s) {
      var mins = (speakers.totals[String(s)] / 60).toFixed(1);
      return '<div class="spk ' + (s === speakers.host ? "host" : "") + '" data-s="' + s + '">'
        + '<span class="tag">' + (s === speakers.host ? "host" : "guest") + "</span>"
        + '<span class="who">Speaker ' + s + "</span>"
        + '<span class="mins">' + mins + " min</span></div>";
    }).join("");
    var d = speakers.host_detection || {};
    $("speakerHint").textContent = d.method === "voiceprint"
      ? "Matched the saved host voiceprint at " + d.confidence + " similarity."
      : "Guessed from who opens the episode and who asks the questions. Click the other "
        + "speaker if that is wrong.";
    $$(".spk").forEach(function (el) {
      el.onclick = function () {
        speakers.host = parseInt(el.dataset.s, 10);
        renderSpeakers();
        runEngine(["--mode", "sethost", "--audio", media(),
                   "--host", String(speakers.host)], function () {
          setStatus("Host set to speaker " + speakers.host + " and remembered", "ok");
        });
      };
    });
  }

  /* ------------------------------------------------------------- step 2 */

  $$("#findMode button").forEach(function (b) {
    b.onclick = function () {
      $$("#findMode button").forEach(function (x) { x.classList.toggle("on", x === b); });
      $("modeAuto").hidden = b.dataset.mode !== "auto";
      $("modeList").hidden = b.dataset.mode !== "list";
    };
  });

  $("allowMixed").onchange = function () {
    $("hostLimitRow").style.opacity = this.checked ? 0.4 : 1;
    $("hostMax").disabled = this.checked;
  };

  function silenceArgs() {
    return ["--silence", $("silenceMode").value,
            "--silence-mode", $("silenceDetect").value]
      .concat($("keepDramatic").checked ? [] : ["--no-protect-dramatic"]);
  }

  $("btnDiscover").onclick = function () {
    if (!media()) return setStatus("Pick the episode first", "err");
    if (!speakers) return setStatus("Run Prepare in step 1 first, speakers are needed", "err");
    $("btnDiscover").disabled = true;
    setStatus("Reading the episode. This asks the model a few times, give it a minute.",
      "busy");
    var out = outPath("cuts.json");
    runEngine(["--mode", "discover", "--audio", media(), "--out", out,
               "--min-count", $("minCount").value, "--max-count", $("maxCount").value,
               "--min-len", $("minLen").value, "--max-len", $("maxLen").value,
               "--target-len", $("targetLen").value,
               "--host-max", $("hostMax").value]
      .concat($("guestOnly").checked ? ["--guest-only"] : [])
      .concat($("allowMixed").checked ? ["--allow-intertwined"] : [])
      .concat(silenceArgs()), function (code) {
      $("btnDiscover").disabled = false;
      if (code !== 0) return setStatus("Discovery failed, see the log", "err");
      loadCuts(out, true);
    });
  };

  $("btnAnalyze").onclick = function () {
    if (!media()) return setStatus("Pick the episode first", "err");
    var text = $("reelText").value.trim();
    if (!text) return setStatus("Paste the list first", "err");
    var reels = outPath("last_reels.txt");
    fs.writeFileSync(reels, text, "utf8");
    $("btnAnalyze").disabled = true;
    setStatus("Reading the list", "busy");
    var out = outPath("cuts.json");
    runEngine(["--mode", "analyze", "--audio", media(), "--reels-file", reels, "--out", out]
      .concat(silenceArgs()), function (code) {
      $("btnAnalyze").disabled = false;
      if (code !== 0) return setStatus("Could not read the list, see the log", "err");
      loadCuts(out, false);
    });
  };

  /* ------------------------------------------------------------- step 3 */

  function loadCuts(file, discovered) {
    try {
      cuts = JSON.parse(fs.readFileSync(file, "utf8"));
    } catch (e) { return setStatus("Could not read results: " + e, "err"); }
    try {
      assets = JSON.parse(fs.readFileSync(file.replace(/\.json$/, ".assets.json"), "utf8"));
    } catch (e) { assets = {}; }

    cuts.forEach(function (c) { c.keep = true; });
    renderMoments(discovered);
    setStatus(cuts.length + (discovered ? " moments found" : " reels matched"), "ok");
    go("review");
  }

  function renderMoments(discovered) {
    var total = cuts.reduce(function (a, c) {
      return a + (c.segments || []).reduce(function (x, s) { return x + (s.out_s - s.in_s); }, 0);
    }, 0);
    $("reviewSummary").textContent = cuts.length + " reels, " + fmt(total) + " total"
      + (discovered ? ", ordered by where they fall in the episode" : "");

    var src = media().replace(/\\/g, "/");
    $("moments").innerHTML = cuts.map(function (c, i) {
      var len = (c.segments || []).reduce(function (x, s) { return x + (s.out_s - s.in_s); }, 0);
      var mix = c.speaker_mix;
      var guestPct = mix ? Math.round(100 * mix.guest_s / Math.max(0.1, mix.guest_s + mix.host_s)) : null;
      var flags = (c.flags || []).map(function (f) {
        return '<span class="flag">' + esc(f) + "</span>";
      }).join("");

      return '<div class="moment kept" data-i="' + i + '">'
        + '<div class="top"><span class="dot"></span>'
        + '<span class="no">' + (i + 1) + "</span>"
        + '<span class="tc">' + fmt(c.in_s) + " to " + fmt(c.out_s) + "</span>"
        + (c.model_score ? '<span class="score">' + c.model_score.toFixed(1) + "</span>" : "")
        + "</div>"
        + (c.summary ? '<p class="sum">' + esc(c.summary) + "</p>" : "")
        + (c.why ? '<p class="why">' + esc(c.why) + "</p>" : "")
        + '<div class="meta">'
        + "<span>" + fmt(len) + " after trimming</span>"
        + (c.saved_s ? "<span>" + c.saved_s.toFixed(1) + "s dead air out</span>" : "")
        + (mix ? '<span class="mixbar" title="guest ' + mix.guest_s + "s, host "
            + mix.host_s + 's"><i style="width:' + guestPct + '%"></i></span>'
            + "<span>host " + mix.host_s + "s</span>" : "")
        + (c.visual ? "<span>visual: " + esc(c.visual) + "</span>" : "")
        + flags + "</div>"
        + '<div class="ctl">'
        + '<button class="ghost tiny" data-act="play">Preview</button>'
        + '<button class="ghost tiny" data-act="in-">in -1s</button>'
        + '<button class="ghost tiny" data-act="in+">in +1s</button>'
        + '<button class="ghost tiny" data-act="out-">out -1s</button>'
        + '<button class="ghost tiny" data-act="out+">out +1s</button>'
        + '<button class="ghost tiny" data-act="drop">Drop</button>'
        + "</div></div>";
    }).join("");

    $$("#moments .moment").forEach(function (el) {
      var i = parseInt(el.dataset.i, 10);
      el.querySelectorAll("button").forEach(function (b) {
        b.onclick = function () { momentAction(el, i, b.dataset.act, src); };
      });
    });
  }

  function momentAction(el, i, act, src) {
    var c = cuts[i];
    if (act === "drop") {
      c.keep = !c.keep;
      el.classList.toggle("kept", c.keep);
      el.classList.toggle("dropped", !c.keep);
      el.querySelector('[data-act="drop"]').textContent = c.keep ? "Drop" : "Keep";
      return;
    }
    if (act === "play") return preview(el, c, src);
    var d = act.indexOf("+") > 0 ? 1 : -1;
    if (act.indexOf("in") === 0) {
      c.in_s = Math.max(0, c.in_s + d);
      if (c.segments && c.segments.length) c.segments[0].in_s = c.in_s;
    } else {
      c.out_s = c.out_s + d;
      if (c.segments && c.segments.length) c.segments[c.segments.length - 1].out_s = c.out_s;
    }
    el.querySelector(".tc").textContent = fmt(c.in_s) + " to " + fmt(c.out_s);
  }

  // Seeks the source file rather than rendering a clip. Needs
  // --allow-file-access-from-files, which the manifest sets.
  function preview(el, c, src) {
    var v = el.querySelector("video");
    if (!v) {
      v = document.createElement("video");
      v.controls = true;
      v.preload = "metadata";
      v.src = "file:///" + encodeURI(src);
      el.appendChild(v);
      v.addEventListener("error", function () {
        v.remove();
        setStatus("Cannot play this file in the panel. The cuts are still correct.", "err");
      });
    }
    var stop = function () { if (v.currentTime >= c.out_s) v.pause(); };
    v.removeEventListener("timeupdate", v._stop || function () {});
    v._stop = stop;
    v.addEventListener("timeupdate", stop);
    var seek = function () { v.currentTime = c.in_s; v.play(); };
    if (v.readyState >= 1) seek();
    else v.addEventListener("loadedmetadata", seek, { once: true });
  }

  $("btnToBuild").onclick = function () {
    var kept = cuts.filter(function (c) { return c.keep; });
    if (!kept.length) return setStatus("Keep at least one reel", "err");
    setStatus(kept.length + " reels kept", "ok");
    go("build");
  };

  function keptCuts() { return cuts.filter(function (c) { return c.keep; }); }

  function reelPayload() {
    return keptCuts().map(function (c, i) {
      return {
        name: "REEL_" + (i + 1 < 10 ? "0" : "") + (i + 1),
        in_s: c.in_s, out_s: c.out_s,
        segments: c.segments || [],
        bed: c.music ? c.music.bed : null,
        note: c.summary || "",
      };
    });
  }

  /* ------------------------------------------------------------- step 4 */

  $("offsetMode").onchange = function () {
    $("manualOffset").disabled = this.value !== "manual";
  };
  $("btnLogo").onclick = function () {
    var r = window.cep.fs.showOpenDialog(false, false, "Pick the logo", null,
      ["png", "psd", "tif", "tiff", "tga", "ai"]);
    if (r && r.data && r.data.length) $("logoPath").value = r.data[0];
  };
  $("btnSongs").onclick = function () {
    var r = window.cep.fs.showOpenDialog(false, true, "Pick the songs folder", null, null);
    if (r && r.data && r.data.length) $("songsPath").value = r.data[0];
  };

  $("btnDiag").onclick = function () {
    setStatus("Asking Premiere what it supports", "busy");
    cs.evalScript("ACP_diagnostics()", function (res) {
      try {
        var r = JSON.parse(res);
        log("\n--- diagnostics ---\n"
          + String(r.ok || r.error).split("  //  ").join("\n") + "\n");
        $("log").hidden = false;
        var m = /effects matching \/reframe\/: (.+?)(?: {2}\/\/ {2}|$)/.exec(r.ok || "");
        if (m && m[1] && m[1] !== "NONE FOUND") {
          effectName = m[1].split(" | ")[0];
          setStatus("Auto Reframe found: " + effectName, "ok");
        } else {
          setStatus("No Auto Reframe effect found, framing will fill and centre", "ok");
        }
      } catch (e) { setStatus("Unexpected reply: " + res, "err"); }
    });
  };

  $("btnBuild").onclick = function () {
    if (!cuts) return;
    var run = function () {
      callHost("ACP_buildReels", {
        mediaPath: media(),
        offsetMode: $("offsetMode").value,
        manualOffset: parseFloat($("manualOffset").value) || 0,
        binName: "AutoCut Reels",
        framing: $("framingMode").value,
        frameX: parseFloat($("frameX").value) || 0.5,
        effectName: effectName,
        onlyFirst: $("onlyFirst").checked,
        alsoCutTimeline: $("alsoCutTimeline").checked,
        logo: assets.logo && assets.logo.placement
          ? { path: assets.logo.path, placement: assets.logo.placement } : null,
        reels: reelPayload(),
      }, "Building in Premiere", function () { go("export"); });
    };

    // Logo and music are prepared by the engine, not the host script.
    if ($("logoPath").value.trim() || $("songsPath").value.trim()) {
      setStatus("Preparing logo and music", "busy");
      runEngine(["--mode", "assets", "--audio", media(), "--out", outPath("cuts.json")]
        .concat($("logoPath").value.trim()
          ? ["--logo", $("logoPath").value.trim(),
             "--logo-width-pct", $("logoWidthPct").value,
             "--logo-x", $("logoX").value, "--logo-y", $("logoY").value] : [])
        .concat($("songsPath").value.trim()
          ? ["--songs", $("songsPath").value.trim()] : []), function () {
        try {
          var fresh = JSON.parse(fs.readFileSync(outPath("cuts.json"), "utf8"));
          fresh.forEach(function (f, i) { if (cuts[i]) cuts[i].music = f.music; });
          assets = JSON.parse(fs.readFileSync(outPath("cuts.assets.json"), "utf8"));
          if (assets.logo && assets.logo.placement) {
            $("logoInfo").textContent = assets.logo.placement.summary;
          }
        } catch (e) {}
        run();
      });
    } else { run(); }
  };

  /* ------------------------------------------------------------- step 5 */

  $("btnExportDir").onclick = function () {
    var r = window.cep.fs.showOpenDialog(false, true, "Pick the export folder", null, null);
    if (r && r.data && r.data.length) $("exportDir").value = r.data[0];
  };

  $("exportName").oninput = function () {
    var n = keptCuts().length;
    $("exportHint").textContent = n
      ? (this.value.trim() || "<name>") + " 001.mp4 through "
        + (this.value.trim() || "<name>") + " " + ("00" + n).slice(-3) + ".mp4"
      : "Files are numbered 001, 002, and so on.";
  };

  $("btnExport").onclick = function () {
    var base = $("exportName").value.trim(), dir = $("exportDir").value.trim();
    if (!base) return setStatus("Give the reels a name first", "err");
    if (!dir) return setStatus("Pick a folder first", "err");
    var mbps = parseFloat($("exportMbps").value) || 10;
    var preset = path.join(config.cacheDir, "AutoCutPR_" + mbps + "Mbps.epr");

    setStatus("Preparing the export preset", "busy");
    var gen = cp.spawn(config.python,
      [path.join(config.engineDir, "preset.py"), "--out", preset, "--mbps", String(mbps)],
      { windowsHide: true });
    gen.stdout.on("data", function (d) { log(d.toString("utf8")); });
    gen.stderr.on("data", function (d) { log(d.toString("utf8")); });
    gen.on("close", function (code) {
      if (code !== 0 || !fs.existsSync(preset)) {
        return setStatus("Export preset failed, see the log", "err");
      }
      $("btnExport").disabled = true;
      callHost("ACP_exportReels", {
        names: reelPayload().map(function (r) { return r.name; }),
        outDir: dir, baseName: base, presetPath: preset,
      }, "Exporting. Premiere is busy until this finishes.",
        function () { go("trailer"); },
        function () { $("btnExport").disabled = false; });
    });
  };

  /* ------------------------------------------------------------- step 6 */

  $("btnTrailer").onclick = function () {
    if (!cuts) return setStatus("Find or read some reels first", "err");
    $("btnTrailer").disabled = true;
    setStatus("Working out a trailer", "busy");
    var out = outPath("trailer.json");
    runEngine(["--mode", "trailer", "--audio", media(),
               "--cuts", outPath("cuts.json"), "--out", out], function (code) {
      $("btnTrailer").disabled = false;
      if (code !== 0) return setStatus("Trailer suggestion failed, see the log", "err");
      try {
        renderTrailer(JSON.parse(fs.readFileSync(out, "utf8")));
        setStatus("Trailer suggestions ready", "ok");
      } catch (e) { setStatus("Could not read the suggestions: " + e, "err"); }
    });
  };

  function renderTrailer(t) {
    var picks = (t.candidates || []).map(function (c, i) {
      return '<div class="moment"><div class="top"><span class="dot"></span>'
        + '<span class="no">' + (i + 1) + "</span>"
        + '<span class="tc">' + fmt(c.in_s) + " to " + fmt(c.out_s) + "</span></div>"
        + '<p class="sum">' + esc(c.summary) + "</p>"
        + '<p class="why">' + esc(c.why || "") + "</p>"
        + (c.visual ? '<div class="meta"><span>visual: ' + esc(c.visual)
            + "</span></div>" : "") + "</div>";
    }).join("");

    var orders = (t.orders || []).map(function (o, i) {
      var clips = o.clips || [];
      var total = clips.reduce(function (a, c) { return a + (c.length || 0); }, 0);
      return '<div class="order"><h3>' + esc(o.name || ("Order " + (i + 1)))
        + ' <span class="tc">' + total.toFixed(0) + "s</span></h3><ol>"
        + clips.map(function (c) {
          return '<li><span class="role">' + esc(c.role) + "</span> "
            + esc(c.summary)
            + (c.length ? ' <span class="tc">' + c.length.toFixed(0) + "s</span>" : "")
            + (c.why ? '<div class="why">' + esc(c.why) + "</div>" : "")
            + "</li>";
        }).join("") + "</ol>"
        + (o.why ? '<p class="why">' + esc(o.why) + "</p>" : "") + "</div>";
    }).join("");

    $("trailerOut").innerHTML =
      '<div class="summary">Five candidates, then three ways to order three of them.</div>'
      + '<div class="moments">' + picks + "</div>" + orders;
  }

  /* --------------------------------------------------------------- boot */

  $("btnLog").onclick = function () { $("log").hidden = !$("log").hidden; };

  loadConfig();
  go("source");
  setStatus("Pick the episode to begin");
})();
