/* AutoCutPR ExtendScript side: project info, timeline razor + markers, subclips.
   ExtendScript is ES3 and has no JSON object — payloads from the panel are
   parsed with eval() (panel-controlled strings only), replies are built by hand. */

var ACP_TICKS_PER_SECOND = 254016000000;
var ACP_MARKER_PURPLE = 2; /* 0 Green 1 Red 2 Purple 3 Orange 4 Yellow 5 White 6 Blue 7 Cyan */

/* Isolated Full Episode bridge; existing reels endpoints stay independent. */
try { $.evalFile(new File(new File($.fileName).parent.fsName + '/episode.jsx')); }
catch (episodeLoadError) { $.writeln('Full Episode bridge: ' + episodeLoadError); }

function ACP_escape(s) {
  return String(s).replace(/\\/g, "\\\\").replace(/"/g, '\\"').replace(/[\r\n]+/g, " ");
}

function ACP_getSelection() {
  try {
    var sel = app.getCurrentProjectViewSelection();
    if (!sel || sel.length === 0) {
      return '{"error":"Nothing selected in the Project panel"}';
    }
    var item = sel[0];
    var mediaPath = "";
    try { mediaPath = item.getMediaPath() || ""; } catch (e) {}
    if (!mediaPath) {
      return '{"error":"Selected item has no media file (multicam/sequence?). Browse to its audio instead."}';
    }
    return '{"name":"' + ACP_escape(item.name) + '","mediaPath":"' + ACP_escape(mediaPath) + '"}';
  } catch (e) {
    return '{"error":"' + ACP_escape(e) + '"}';
  }
}

/* ---------- helpers ---------- */

function ACP_seqFps(seq) {
  try {
    var fr = seq.getSettings().videoFrameRate;
    var fps = 1 / fr.seconds;
    return Math.round(fps * 100) / 100;
  } catch (e) {
    return 25;
  }
}

function ACP_pad(n, w) {
  var s = String(Math.floor(n));
  while (s.length < w) s = "0" + s;
  return s;
}

/* Non-drop timecode string for the QE razor. */
function ACP_secondsToTimecode(sec, fps) {
  if (sec < 0) sec = 0;
  var totalFrames = Math.round(sec * fps);
  var fpsI = Math.round(fps);
  var f = totalFrames % fpsI;
  var totalSeconds = (totalFrames - f) / fpsI;
  var s = totalSeconds % 60;
  var totalMinutes = (totalSeconds - s) / 60;
  var m = totalMinutes % 60;
  var h = (totalMinutes - m) / 60;
  return ACP_pad(h, 2) + ":" + ACP_pad(m, 2) + ":" + ACP_pad(s, 2) + ":" + ACP_pad(f, 2);
}

function ACP_eachTrack(seq, fn) {
  var i;
  for (i = 0; i < seq.videoTracks.numTracks; i++) fn(seq.videoTracks[i]);
  for (i = 0; i < seq.audioTracks.numTracks; i++) fn(seq.audioTracks[i]);
}

/* Find where the analyzed media sits in the sequence, so master-audio time can
   be converted to timeline time. Returns seconds to ADD to a source time, or
   null when the media isn't present in this sequence. */
function ACP_detectOffset(seq, mediaPath) {
  var target = String(mediaPath).toLowerCase();
  var found = null;
  ACP_eachTrack(seq, function (track) {
    if (found) return;
    for (var c = 0; c < track.clips.numItems; c++) {
      var clip = track.clips[c];
      try {
        var pi = clip.projectItem;
        if (!pi) continue;
        var p = pi.getMediaPath();
        if (p && String(p).toLowerCase() === target) {
          found = {
            offset: clip.start.seconds - clip.inPoint.seconds,
            clipName: clip.name,
          };
          return;
        }
      } catch (e) {}
    }
  });
  return found;
}

/* ---------- main action: razor the timeline + mark each reel ---------- */

/* payload: {mediaPath, offsetMode:"auto"|"manual", manualOffset:num,
             razor:bool, cuts:[{name,in_s,out_s,note}]} */
function ACP_cutOnTimeline(payloadStr) {
  try {
    return ACP_cutOnTimelineObj(eval("(" + payloadStr + ")"));
  } catch (e) {
    return '{"error":"' + ACP_escape(e) + '"}';
  }
}

/* ES3 has no JSON.stringify, so callers inside this file pass the object
   straight through instead of re-serialising it. */
function ACP_cutOnTimelineObj(payload) {
  try {
    var seq = app.project.activeSequence;
    if (!seq) return '{"error":"No active sequence — open the episode timeline first"}';

    var offset = 0;
    var offsetNote = "";
    if (payload.offsetMode === "manual") {
      offset = Number(payload.manualOffset) || 0;
      offsetNote = "manual offset " + offset.toFixed(2) + "s";
    } else {
      var det = ACP_detectOffset(seq, payload.mediaPath);
      if (det === null) {
        return '{"error":"The analyzed media is not in this sequence, so its timeline ' +
               'position is unknown. Put the master audio on the timeline, or switch to ' +
               'a manual offset."}';
      }
      offset = det.offset;
      offsetNote = "auto offset " + offset.toFixed(2) + "s (from '" + ACP_escape(det.clipName) + "')";
    }

    var fps = ACP_seqFps(seq);
    var zero = 0;
    try { zero = seq.zeroPoint / ACP_TICKS_PER_SECOND; } catch (eZ) {}

    var razored = 0;
    var marked = 0;
    var problems = [];

    /* Razor first: adding markers doesn't disturb clips, but razoring shifts
       nothing either — order only matters for reporting. */
    if (payload.razor) {
      var qseq = null;
      try {
        app.enableQE();
        qseq = qe.project.getActiveSequence();
      } catch (eQE) {
        problems.push("razor unavailable (QE DOM): " + eQE);
      }
      if (qseq) {
        for (var i = 0; i < payload.cuts.length; i++) {
          var c = payload.cuts[i];
          var points = [c.in_s + offset + zero, c.out_s + offset + zero];
          for (var p = 0; p < points.length; p++) {
            var tc = ACP_secondsToTimecode(points[p], fps);
            var didOne = false;
            for (var v = 0; v < qseq.numVideoTracks; v++) {
              try { qseq.getVideoTrackAt(v).razor(tc); didOne = true; } catch (eV) {}
            }
            for (var a = 0; a < qseq.numAudioTracks; a++) {
              try { qseq.getAudioTrackAt(a).razor(tc); didOne = true; } catch (eA) {}
            }
            if (didOne) razored++;
          }
        }
      }
    }

    /* Duration markers, purple — the API cannot colour timeline clips. */
    for (var j = 0; j < payload.cuts.length; j++) {
      var cut = payload.cuts[j];
      try {
        var mIn = cut.in_s + offset + zero;
        var mOut = cut.out_s + offset + zero;
        var marker = seq.markers.createMarker(mIn);
        marker.name = cut.name;
        if (cut.note) marker.comments = cut.note;
        try { marker.end = mOut; } catch (eEnd) {}
        try {
          marker.setColorByIndex(ACP_MARKER_PURPLE);
        } catch (eC1) {
          try { marker.setColorByIndex(ACP_MARKER_PURPLE, j); } catch (eC2) {}
        }
        marked++;
      } catch (eM) {
        problems.push(cut.name + ": " + eM);
      }
    }

    var msg = marked + "/" + payload.cuts.length + " reels marked (purple)";
    if (payload.razor) msg += ", " + razored + " razor points cut";
    msg += " | " + offsetNote + " | " + fps + " fps";
    if (problems.length) msg += " | issues: " + problems.join("; ");
    return '{"ok":"' + ACP_escape(msg) + '"}';
  } catch (e) {
    return '{"error":"' + ACP_escape(e) + '"}';
  }
}

/* ---------- export ---------- */

function ACP_pad3(n) {
  var s = String(n);
  while (s.length < 3) s = "0" + s;
  return s;
}

function ACP_findSequenceByName(name) {
  for (var i = 0; i < app.project.sequences.numSequences; i++) {
    var s = app.project.sequences[i];
    if (s.name === name) return s;
  }
  return null;
}

/* payload: {names:[seqName,...], outDir, baseName, presetPath} */
function ACP_exportReels(payloadStr) {
  try {
    var payload = eval("(" + payloadStr + ")");

    var presetFile = new File(payload.presetPath);
    if (!presetFile.exists) {
      return '{"error":"export preset not found: ' + ACP_escape(payload.presetPath) + '"}';
    }
    var dir = new Folder(payload.outDir);
    if (!dir.exists && !dir.create()) {
      return '{"error":"could not create output folder: ' + ACP_escape(payload.outDir) + '"}';
    }

    var done = 0;
    var problems = [];
    var written = [];
    for (var i = 0; i < payload.names.length; i++) {
      var seq = ACP_findSequenceByName(payload.names[i]);
      if (!seq) {
        problems.push(payload.names[i] + ": sequence not found — build the reels first");
        continue;
      }
      var outName = payload.baseName + " " + ACP_pad3(i + 1) + ".mp4";
      var outPath = dir.fsName + "\\" + outName;
      try {
        /* 0 = entire sequence. Renders in Premiere and blocks until finished;
           Media Encoder is not installed here so app.encoder is not an option. */
        var res = seq.exportAsMediaDirect(outPath, presetFile.fsName, 0);
        if (new File(outPath).exists) {
          done++;
          written.push(outName);
        } else {
          problems.push(outName + ": no file produced (" + res + ")");
        }
      } catch (eEx) {
        problems.push(outName + ": " + eEx);
      }
    }

    var msg = "Exported " + done + "/" + payload.names.length + " reels to " + payload.outDir;
    if (written.length) {
      msg += " | " + written[0] + (written.length > 1 ? " … " + written[written.length - 1] : "");
    }
    if (problems.length) msg += " | issues: " + problems.join("; ");
    return '{"ok":"' + ACP_escape(msg) + '"}';
  } catch (e) {
    return '{"error":"' + ACP_escape(e) + '"}';
  }
}

/* ---------- diagnostics: probe the undocumented API surface ---------- */

function ACP_diagnostics() {
  var out = [];
  function line(s) { out.push(String(s)); }

  try {
    line("Premiere " + app.version);
    var seq = app.project.activeSequence;
    if (!seq) {
      line("NO ACTIVE SEQUENCE");
    } else {
      line("sequence: " + seq.name + " @ " + ACP_seqFps(seq) + " fps");
      try {
        var st = seq.getSettings();
        line("frame: " + st.videoFrameWidth + "x" + st.videoFrameHeight);
        line("setSettings: " + (typeof seq.setSettings === "function" ? "yes" : "NO"));
      } catch (e1) { line("getSettings FAILED: " + e1); }
      line("createSubsequence: " + (typeof seq.createSubsequence === "function" ? "yes" : "NO"));
      line("projectItem: " + (seq.projectItem ? "yes" : "NO"));
      try {
        line("in/out: " + seq.getInPoint() + " .. " + seq.getOutPoint());
      } catch (e2) { line("getInPoint FAILED: " + e2); }
    }

    /* which mediaType argument does setInPoint accept here? */
    try {
      var probe = null;
      if (seq && seq.projectItem) probe = seq.projectItem;
      if (probe) {
        var accepted = [];
        var types = [1, 2, 4];
        for (var t = 0; t < types.length; t++) {
          try { probe.setInPoint(0, types[t]); accepted.push(types[t]); } catch (eT) {}
        }
        line("setInPoint mediaType accepted: " + (accepted.length ? accepted.join(",") : "none"));
      }
    } catch (e3) { line("setInPoint probe failed: " + e3); }

    /* QE + effect names */
    try {
      app.enableQE();
      line("QE: available");
      var names = qe.project.getVideoEffectList();
      var hits = [];
      for (var i = 0; i < names.length; i++) {
        var n = String(names[i].name ? names[i].name : names[i]);
        if (/reframe|recadr/i.test(n)) hits.push(n);
      }
      line("effects matching /reframe/: " + (hits.length ? hits.join(" | ") : "NONE FOUND"));
      line("total video effects: " + names.length);
    } catch (e4) {
      line("QE FAILED: " + e4);
    }
  } catch (e) {
    line("diagnostics error: " + e);
  }

  return '{"ok":"' + ACP_escape(out.join("  //  ")) + '"}';
}

/* ---------- main build: one 9:16 sequence per reel ---------- */

function ACP_setInOutOnItem(pi, inSec, outSec, mediaType) {
  /* mediaType is required on setOutPoint; which value works varies by build. */
  var types = mediaType ? [mediaType] : [4, 1, 2];
  for (var i = 0; i < types.length; i++) {
    try {
      pi.setInPoint(inSec, types[i]);
      pi.setOutPoint(outSec, types[i]);
      return types[i];
    } catch (e) {}
  }
  try {
    pi.setInPoint(inSec);
    pi.setOutPoint(outSec);
    return 0;
  } catch (e2) {}
  return -1;
}

function ACP_findEffect(candidates) {
  try {
    for (var i = 0; i < candidates.length; i++) {
      var fx = qe.project.getVideoEffectByName(candidates[i]);
      if (fx) return { fx: fx, name: candidates[i] };
    }
  } catch (e) {}
  return null;
}

function ACP_applyFill(seq, frameX) {
  /* 16:9 source filling a 9:16 frame: scale so height covers. frameX shifts the
     visible window horizontally — the speaker is rarely dead centre in the
     original wide shot, so 0.5 leaves them off to one side. */
  var x = frameX === undefined ? 0.5 : frameX;
  var applied = 0;
  for (var t = 0; t < seq.videoTracks.numTracks; t++) {
    var track = seq.videoTracks[t];
    for (var c = 0; c < track.clips.numItems; c++) {
      var clip = track.clips[c];
      try {
        for (var m = 0; m < clip.components.numItems; m++) {
          var comp = clip.components[m];
          if (comp.displayName !== "Motion") continue;
          for (var p = 0; p < comp.properties.numItems; p++) {
            var prop = comp.properties[p];
            if (prop.displayName === "Scale") prop.setValue(177.8, true);
            if (prop.displayName === "Position") prop.setValue([x, 0.5], true);
          }
          applied++;
        }
      } catch (e) {}
    }
  }
  return applied;
}

/* Shift horizontal position without touching whatever else is set. */
function ACP_nudgeX(seq, frameX) {
  var applied = 0;
  for (var t = 0; t < seq.videoTracks.numTracks; t++) {
    var track = seq.videoTracks[t];
    for (var c = 0; c < track.clips.numItems; c++) {
      try {
        var clip = track.clips[c];
        for (var m = 0; m < clip.components.numItems; m++) {
          var comp = clip.components[m];
          if (comp.displayName !== "Motion") continue;
          for (var p = 0; p < comp.properties.numItems; p++) {
            var prop = comp.properties[p];
            if (prop.displayName !== "Position") continue;
            var cur = null;
            try { cur = prop.getValue(); } catch (eG) {}
            var y = (cur && cur.length > 1) ? cur[1] : 0.5;
            prop.setValue([frameX, y], true);
            applied++;
          }
        }
      } catch (e) {}
    }
  }
  return applied;
}

function ACP_applyAutoReframe(seq, effectName) {
  var found = ACP_findEffect(
    effectName ? [effectName] :
    ["Auto Reframe", "ADBE Auto Reframe", "AE.ADBE Auto Reframe", "Auto-Reframe"]
  );
  if (!found) return { applied: 0, name: null };

  var applied = 0;
  try {
    app.project.openSequence(seq.sequenceID);
    var qseq = qe.project.getActiveSequence();
    for (var t = 0; t < qseq.numVideoTracks; t++) {
      var qtrack = qseq.getVideoTrackAt(t);
      for (var c = 0; c < qtrack.numItems; c++) {
        try {
          qtrack.getItemAt(c).addVideoEffect(found.fx);
          applied++;
        } catch (eItem) {}
      }
    }
  } catch (e) {}
  return { applied: applied, name: found.name };
}

/* ---------- logo + music placement ---------- */

function ACP_importAndFind(filePath, bin) {
  var existing = ACP_findItemByPath(app.project.rootItem, filePath);
  if (existing) return existing;
  try {
    app.project.importFiles([filePath], true, bin || app.project.rootItem, false);
  } catch (e) {
    return null;
  }
  return ACP_findItemByPath(app.project.rootItem, filePath);
}

/* Sequences made by createNewSequenceFromClips can come with a single track;
   the logo and the music each need one of their own above/below the content. */
function ACP_ensureTracks(seq, wantVideo, wantAudio) {
  if (seq.videoTracks.numTracks >= wantVideo && seq.audioTracks.numTracks >= wantAudio) {
    return true;
  }
  try {
    app.enableQE();
    app.project.openSequence(seq.sequenceID);
    var qseq = qe.project.getActiveSequence();
    var addV = Math.max(0, wantVideo - seq.videoTracks.numTracks);
    var addA = Math.max(0, wantAudio - seq.audioTracks.numTracks);
    qseq.addTracks(addV, seq.videoTracks.numTracks, addA, 1, seq.audioTracks.numTracks, 0);
  } catch (e) {}
  return seq.videoTracks.numTracks >= wantVideo && seq.audioTracks.numTracks >= wantAudio;
}

/* Seconds -> whole frames and back. Every edit point must sit exactly on a
   frame: Premiere rounds each clip's in/out independently, so advancing a
   playhead by float seconds leaves a 1-frame hole wherever the rounding of the
   placement disagrees with the rounding of the duration. */
function ACP_toFrames(sec, fps) {
  return Math.round(sec * fps);
}

function ACP_frameSec(frames, fps) {
  return frames / fps;
}

function ACP_setMotion(clip, scale, position) {
  var done = false;
  try {
    for (var m = 0; m < clip.components.numItems; m++) {
      var comp = clip.components[m];
      if (comp.displayName !== "Motion") continue;
      for (var p = 0; p < comp.properties.numItems; p++) {
        var prop = comp.properties[p];
        if (prop.displayName === "Scale") prop.setValue(scale, true);
        if (prop.displayName === "Position") prop.setValue(position, true);
      }
      done = true;
    }
  } catch (e) {}
  return done;
}

function ACP_placeLogo(seq, logoPI, lengthSec, placement) {
  if (!ACP_ensureTracks(seq, 2, 1)) return "no second video track for the logo";
  /* Stills need an explicit out point or they land at the default duration. */
  ACP_setInOutOnItem(logoPI, 0, lengthSec, null);
  var track = seq.videoTracks[seq.videoTracks.numTracks - 1];
  try {
    track.overwriteClip(logoPI, 0);
  } catch (e) {
    return "logo insert failed: " + e;
  }
  var clip = track.clips.numItems ? track.clips[track.clips.numItems - 1] : null;
  if (!clip) return "logo clip not found after insert";
  /* scale is computed against a frame-fitted source, so pin that down */
  try { logoPI.setScaleToFrameSize(); } catch (eS) {}
  if (!ACP_setMotion(clip, placement.scale, placement.position)) {
    return "logo placed but Motion could not be set";
  }
  return null;
}

function ACP_placeMusic(seq, bedPI, lengthSec) {
  if (!ACP_ensureTracks(seq, 1, 2)) return "no second audio track for the music";
  ACP_setInOutOnItem(bedPI, 0, lengthSec, null);
  var track = seq.audioTracks[seq.audioTracks.numTracks - 1];
  try {
    track.overwriteClip(bedPI, 0);
  } catch (e) {
    return "music insert failed: " + e;
  }
  return null;
}

/* payload: {mediaPath, offsetMode, manualOffset, binName, framing:"autoreframe"|"fill",
             effectName:str|null, onlyFirst:bool,
             logo:{path, placement:{scale, position}}|null,
             reels:[{name, in_s, out_s, segments:[{in_s,out_s}], bed:str|null}]} */
function ACP_buildReels(payloadStr) {
  var master = null;
  var savedIn = null, savedOut = null;
  try {
    var payload = eval("(" + payloadStr + ")");
    master = app.project.activeSequence;
    if (!master) return '{"error":"No active sequence — open the episode timeline first"}';
    if (typeof master.createSubsequence !== "function") {
      return '{"error":"This Premiere build has no createSubsequence() — cannot nest by script"}';
    }

    var offset = 0;
    if (payload.offsetMode === "manual") {
      offset = Number(payload.manualOffset) || 0;
    } else {
      var det = ACP_detectOffset(master, payload.mediaPath);
      if (det === null) {
        return '{"error":"The analyzed media is not in this sequence, so its timeline ' +
               'position is unknown. Put the master audio on the timeline, or switch to ' +
               'a manual offset."}';
      }
      offset = det.offset;
    }

    try { savedIn = master.getInPointAsTime().seconds; } catch (eI) {}
    try { savedOut = master.getOutPointAsTime().seconds; } catch (eO) {}

    var bin = ACP_findOrCreateBin(payload.binName);
    var srcBin = null;
    try {
      for (var b = 0; b < bin.children.numItems; b++) {
        if (bin.children[b].name === "_src") { srcBin = bin.children[b]; break; }
      }
      if (!srcBin) srcBin = bin.createBin("_src");
    } catch (eBin) { srcBin = bin; }

    var reels = payload.reels;
    if (payload.onlyFirst && reels.length > 1) reels = [reels[0]];

    var built = 0;
    var notes = [];
    var framingName = null;
    var timelineMsg = "";

    /* Razor + mark the master timeline first, so the episode edit shows where
       every reel came from before the reels themselves are built. Done up front
       because createSubsequence below moves the master's in/out points. */
    if (payload.alsoCutTimeline) {
      var tlPayload = {
        mediaPath: payload.mediaPath,
        offsetMode: payload.offsetMode,
        manualOffset: payload.manualOffset,
        razor: true,
        cuts: payload.reels,
      };
      try {
        var tlRes = eval("(" + ACP_cutOnTimelineObj(tlPayload) + ")");
        timelineMsg = tlRes.ok ? (" | timeline: " + tlRes.ok) : (" | timeline FAILED: " + tlRes.error);
      } catch (eTL) {
        timelineMsg = " | timeline FAILED: " + eTL;
      }
    }

    /* import the logo once for the whole batch */
    var logoPI = null;
    if (payload.logo && payload.logo.path) {
      logoPI = ACP_importAndFind(payload.logo.path, bin);
      if (!logoPI) notes.push("logo could not be imported: " + payload.logo.path);
    }

    for (var r = 0; r < reels.length; r++) {
      var reel = reels[r];
      try {
        /* 1. nest the reel region out of the master, edit intact */
        master.setInPoint(reel.in_s + offset);
        master.setOutPoint(reel.out_s + offset);
        var sub = master.createSubsequence(true);
        if (!sub || !sub.projectItem) {
          notes.push(reel.name + ": createSubsequence returned nothing");
          continue;
        }
        var subPI = sub.projectItem;
        try { subPI.name = reel.name + "_src"; } catch (eN) {}
        try { subPI.moveBin(srcBin); } catch (eMv) {}

        /* 2. lay the speech segments back-to-back; dead air never exists */
        var segs = reel.segments && reel.segments.length
          ? reel.segments
          : [{ in_s: reel.in_s, out_s: reel.out_s }];

        /* all edit points snapped to whole frames — see ACP_toFrames.
           Clamped to >= 0: the nest starts at the reel's in-point, so a
           negative source in-point renders as blank frames at the head. */
        var fps = ACP_seqFps(master);
        var nestF = ACP_toFrames(reel.out_s - reel.in_s, fps);
        var seg0InF = Math.max(0, ACP_toFrames(segs[0].in_s - reel.in_s, fps));
        var seg0OutF = Math.min(nestF, ACP_toFrames(segs[0].out_s - reel.in_s, fps));
        var mt = ACP_setInOutOnItem(
          subPI, ACP_frameSec(seg0InF, fps), ACP_frameSec(seg0OutF, fps), null
        );
        if (mt < 0) {
          notes.push(reel.name + ": could not set in/out on the nest");
          continue;
        }

        var newSeq = app.project.createNewSequenceFromClips(reel.name, [subPI], bin);
        if (!newSeq) {
          notes.push(reel.name + ": createNewSequenceFromClips failed");
          continue;
        }

        /* cursor is counted in FRAMES, so each clip starts exactly where the
           previous one ended — no accumulated float drift, no 1-frame gaps */
        var cursorF = seg0OutF - seg0InF;
        for (var s = 1; s < segs.length; s++) {
          var relInF = Math.max(0, ACP_toFrames(segs[s].in_s - reel.in_s, fps));
          var relOutF = Math.min(nestF, ACP_toFrames(segs[s].out_s - reel.in_s, fps));
          if (relOutF <= relInF) continue;
          if (ACP_setInOutOnItem(
                subPI, ACP_frameSec(relInF, fps), ACP_frameSec(relOutF, fps),
                mt > 0 ? mt : null) < 0) continue;
          try {
            newSeq.videoTracks[0].overwriteClip(subPI, ACP_frameSec(cursorF, fps));
            cursorF += relOutF - relInF;
          } catch (eOv) {
            notes.push(reel.name + " seg" + (s + 1) + ": " + eOv);
          }
        }

        /* 3. make it 9:16 */
        try {
          var settings = newSeq.getSettings();
          settings.videoFrameWidth = 1080;
          settings.videoFrameHeight = 1920;
          newSeq.setSettings(settings);
        } catch (eSet) {
          notes.push(reel.name + ": setSettings failed (" + eSet + ")");
        }

        /* 4. frame it */
        var frameX = payload.frameX === undefined ? 0.5 : payload.frameX;
        if (payload.framing === "fill") {
          ACP_applyFill(newSeq, frameX);
          framingName = "fill-and-centre";
        } else {
          var ar = ACP_applyAutoReframe(newSeq, payload.effectName);
          if (ar.applied > 0) {
            framingName = ar.name;
            /* Auto Reframe tracks the subject but can settle off-centre; the
               nudge rides on top of whatever it decided. */
            if (frameX !== 0.5) ACP_nudgeX(newSeq, frameX);
          } else {
            ACP_applyFill(newSeq, frameX);
            framingName = "fill-and-centre (Auto Reframe unavailable)";
          }
        }

        /* 5. logo + music, both sized to the finished reel — cursorF is the
              running length in frames after dead air was left out */
        var reelLen = ACP_frameSec(cursorF, fps);
        if (logoPI) {
          var logoErr = ACP_placeLogo(newSeq, logoPI, reelLen, payload.logo.placement);
          if (logoErr) notes.push(reel.name + ": " + logoErr);
        }
        if (reel.bed) {
          var bedPI = ACP_importAndFind(reel.bed, bin);
          if (!bedPI) {
            notes.push(reel.name + ": music bed not imported");
          } else {
            var musicErr = ACP_placeMusic(newSeq, bedPI, reelLen);
            if (musicErr) notes.push(reel.name + ": " + musicErr);
          }
        }

        try { newSeq.projectItem.moveBin(bin); } catch (eMb) {}
        built++;
      } catch (eReel) {
        notes.push(reel.name + ": " + eReel);
      }
    }

    var msg = "Built " + built + "/" + reels.length + " reels into '" + payload.binName + "'";
    msg += timelineMsg;
    if (framingName) msg += " | framing: " + framingName;
    if (notes.length) msg += " | issues: " + notes.join("; ");
    return '{"ok":"' + ACP_escape(msg) + '"}';
  } catch (e) {
    return '{"error":"' + ACP_escape(e) + '"}';
  } finally {
    /* never leave the editor's master in/out points moved */
    try {
      if (master && savedIn !== null) master.setInPoint(savedIn);
      if (master && savedOut !== null) master.setOutPoint(savedOut);
    } catch (eR) {}
  }
}

/* ---------- secondary action: subclips in a bin ---------- */

function ACP_findItemByPath(root, mediaPath) {
  var target = String(mediaPath).toLowerCase();
  for (var i = 0; i < root.children.numItems; i++) {
    var child = root.children[i];
    try {
      var p = child.getMediaPath();
      if (p && String(p).toLowerCase() === target) return child;
    } catch (e) {}
    if (child.children && child.children.numItems > 0) {
      var found = ACP_findItemByPath(child, mediaPath);
      if (found) return found;
    }
  }
  return null;
}

function ACP_findOrCreateBin(name) {
  var root = app.project.rootItem;
  for (var i = 0; i < root.children.numItems; i++) {
    var child = root.children[i];
    if (child.type === ProjectItemType.BIN && child.name === name) return child;
  }
  return root.createBin(name);
}

function ACP_createSubclips(payloadStr) {
  try {
    var payload = eval("(" + payloadStr + ")");
    var item = ACP_findItemByPath(app.project.rootItem, payload.mediaPath);
    if (!item) {
      return '{"error":"Media not found in this project: ' + ACP_escape(payload.mediaPath) +
             '. Import it first, then retry."}';
    }
    /* New trackItems inherit their projectItem's label, so colour the source
       violet before subclipping — that is the only way to get violet clips. */
    try { item.setColorLabel(0); } catch (eL) {}

    var bin = ACP_findOrCreateBin(payload.binName);
    var made = 0;
    var errors = [];
    for (var i = 0; i < payload.cuts.length; i++) {
      var c = payload.cuts[i];
      var inTicks = String(Math.round(c.in_s * ACP_TICKS_PER_SECOND));
      var outTicks = String(Math.round(c.out_s * ACP_TICKS_PER_SECOND));
      var sub = null;
      try {
        sub = item.createSubClip(c.name, inTicks, outTicks, 0, 1, 1);
      } catch (eVid) {
        try {
          sub = item.createSubClip(c.name, inTicks, outTicks, 0, 0, 1);
        } catch (eAud) {
          errors.push(c.name + ": " + eAud);
        }
      }
      if (sub) {
        try { sub.setColorLabel(0); } catch (eL2) {}
        try { sub.moveBin(bin); } catch (eMove) {}
        made++;
      }
    }
    var msg = "Created " + made + "/" + payload.cuts.length + ' subclips in bin \'' +
              payload.binName + "'";
    if (errors.length) msg += " | errors: " + errors.join("; ");
    return '{"ok":"' + ACP_escape(msg) + '"}';
  } catch (e) {
    return '{"error":"' + ACP_escape(e) + '"}';
  }
}
