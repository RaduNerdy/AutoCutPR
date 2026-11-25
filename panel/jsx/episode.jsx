/* Full Episode bridge. ES3 only. Never evaluate job-file contents as code. */
var EP = {};
EP.quote = function (s) {
  return '"' + String(s).replace(/["\\\u0000-\u001f]/g, function (c) {
    var escapes = {'"':'\\"', '\\':'\\\\', '\n':'\\n', '\r':'\\r', '\t':'\\t'};
    return escapes[c] || '\\u' + ('0000' + c.charCodeAt(0).toString(16)).slice(-4);
  }) + '"';
};
EP.stringify = function (x) {
  if (x === null || typeof x === 'undefined') return 'null';
  if (typeof x === 'string') return EP.quote(x);
  if (typeof x === 'number') return isFinite(x) ? String(x) : 'null';
  if (typeof x === 'boolean') return String(x);
  var a = [], k, i;
  if (x instanceof Array) {
    for (i = 0; i < x.length; i++) a.push(EP.stringify(x[i]));
    return '[' + a.join(',') + ']';
  }
  for (k in x) if (x.hasOwnProperty(k)) a.push(EP.quote(k) + ':' + EP.stringify(x[k]));
  return '{' + a.join(',') + '}';
};
EP.parse = function (text) {
  var pos = 0;
  function ws() { while (/\s/.test(text.charAt(pos)) && pos < text.length) pos++; }
  function value(depth) {
    if (depth > 100) throw Error('JSON nesting too deep');
    ws(); var c = text.charAt(pos++), out, key, m, code;
    if (c === '"') {
      out = '';
      while (pos < text.length) {
        c = text.charAt(pos++);
        if (c === '"') return out;
        if (c === '\\') {
          c = text.charAt(pos++);
          if (c === 'u') {
            code = text.substr(pos, 4);
            if (!/^[a-fA-F0-9]{4}$/.test(code)) throw Error('Invalid JSON escape');
            out += String.fromCharCode(parseInt(code, 16)); pos += 4;
          } else {
            m = {'"':'"', '\\':'\\', '/':'/', b:'\b', f:'\f', n:'\n', r:'\r', t:'\t'};
            if (!m.hasOwnProperty(c)) throw Error('Invalid JSON escape');
            out += m[c];
          }
        } else {
          if (c.charCodeAt(0) < 32) throw Error('Invalid JSON string');
          out += c;
        }
      }
      throw Error('Unclosed JSON string');
    }
    if (c === '{' || c === '[') {
      out = c === '{' ? {} : []; ws();
      if (text.charAt(pos) === (c === '{' ? '}' : ']')) { pos++; return out; }
      while (true) {
        if (c === '{') {
          ws(); if (text.charAt(pos) !== '"') throw Error('Invalid JSON key');
          key = value(depth+1);
          if (key === '__proto__' || key === 'constructor' || key === 'prototype') throw Error('Unsafe JSON key');
          ws(); if (text.charAt(pos++) !== ':') throw Error('Invalid JSON object');
          out[key] = value(depth+1);
        } else out.push(value(depth+1));
        ws(); key = text.charAt(pos++);
        if (key === (c === '{' ? '}' : ']')) return out;
        if (key !== ',') throw Error('Invalid JSON separator');
      }
    }
    pos--;
    m = /^(true|false|null|-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?)/.exec(text.substr(pos));
    if (!m) throw Error('Invalid JSON value');
    pos += m[0].length;
    return m[0] === 'true' ? true : m[0] === 'false' ? false : m[0] === 'null' ? null : Number(m[0]);
  }
  var result = value(0); ws(); if (pos !== text.length) throw Error('Trailing JSON content'); return result;
};
EP.reply = function (fn, payload) {
  try { return EP.stringify(fn(EP.parse(payload || '{}'))); }
  catch (e) { return EP.stringify({error: String(e)}); }
};
EP.read = function (path) {
  var f = new File(path); f.encoding = 'UTF-8';
  if (!f.open('r')) throw Error('Cannot read ' + path);
  var text; try { text = f.read(); } finally { f.close(); }
  return EP.parse(text.replace(/^\uFEFF/, ''));
};
EP.time = function (ticks) { var t = new Time(); t.ticks = String(ticks); return t; };
EP.compare = function (a, b) {
  a = String(a).replace(/^0+(?=\d)/, ''); b = String(b).replace(/^0+(?=\d)/, '');
  if (!/^\d+$/.test(a) || !/^\d+$/.test(b)) throw Error('Invalid ticks');
  return a.length !== b.length ? (a.length < b.length ? -1 : 1) : a === b ? 0 : a < b ? -1 : 1;
};
EP.addTicks = function (a,b) {
  a=String(a);b=String(b);var i=a.length-1,j=b.length-1,carry=0,out='';
  while(i>=0 || j>=0 || carry) {var n=(i>=0?Number(a.charAt(i--)):0)+(j>=0?Number(b.charAt(j--)):0)+carry;out=String(n%10)+out;carry=Math.floor(n/10);}
  return out || '0';
};
EP.subtractTicks = function (a,b) {
  if(EP.compare(a,b)<0) throw Error('Negative tick difference');
  a=String(a);b=String(b);var i=a.length-1,j=b.length-1,borrow=0,out='';
  while(i>=0) {var n=Number(a.charAt(i--))-(j>=0?Number(b.charAt(j--)):0)-borrow;borrow=n<0?1:0;if(n<0)n+=10;out=String(n)+out;}
  return out.replace(/^0+(?=\d)/,'');
};
EP.inside = function (a, b, x, y) { return EP.compare(a,x) >= 0 && EP.compare(b,y) <= 0; };
EP.sequence = function (id) {
  for (var i=0; i<app.project.sequences.numSequences; i++) {
    var s=app.project.sequences[i]; if (String(s.sequenceID) === String(id)) return s;
  }
  throw Error('Sequence no longer exists');
};
EP.props = function (component) {
  var out=[];
  for (var i=0; i<component.properties.numItems; i++) {
    var p=component.properties[i], animated=false, v=null;
    try { animated=p.isTimeVarying(); v=p.getValue(); } catch(e) { v='unreadable'; }
    out.push({name:p.displayName, value:v, animated:animated});
  }
  return out;
};
EP.clips = function (track, strict) {
  var out=[];
  for (var i=0; i<track.clips.numItems; i++) {
    var c=track.clips[i], pi=c.projectItem, effects=[], unsupported='', path='';
    try { path=pi.getMediaPath(); } catch(e) {}
    if (!path || pi.isSequence() || (pi.isMulticamClip && pi.isMulticamClip())) unsupported='Nested/multicam/non-file source';
    var speed=0, reverse=true;
    try { speed=c.getSpeed(); reverse=c.isSpeedReversed(); } catch(e2) { unsupported='Speed cannot be inspected'; }
    for (var j=0; j<c.components.numItems; j++) {
      var comp=c.components[j], props=EP.props(comp);
      effects.push({name:comp.displayName, matchName:comp.matchName, properties:props});
      if (strict) {
        var allowed = /^(AE\.ADBE (Motion|Opacity|Time Remapping|Lumetri)|ADBE Lumetri)$/.test(comp.matchName);
        if (!allowed) unsupported='Unverified picture effect: '+comp.displayName;
        for (var p=0; p<props.length; p++) if (props[p].animated) unsupported='Animated picture effect: '+comp.displayName;
      }
    }
    out.push({id:String(c.nodeId), name:c.name, itemID:String(pi.nodeId), path:path,
      start:String(c.start.ticks), end:String(c.end.ticks), sourceIn:String(c.inPoint.ticks),
      sourceOut:String(c.outPoint.ticks), speed:speed, reverse:reverse, disabled:!!c.disabled,
      effects:effects, unsupported:unsupported});
  }
  if (strict && track.transitions && track.transitions.numItems) {
    for (i=0;i<out.length;i++) out[i].unsupported='Track transitions require a clean test sequence';
  }
  if (strict && track.isMuted && track.isMuted()) throw Error('V1/V2 is muted. Enable both source tracks first.');
  return out;
};
EP.audio = function (s) {
  var tracks=[];
  for (var i=0;i<s.audioTracks.numTracks;i++) tracks.push({muted:s.audioTracks[i].isMuted(), clips:EP.clips(s.audioTracks[i],false)});
  return tracks;
};
EP.snapshot = function (s, media) {
  if (!s || s.videoTracks.numTracks < 2) throw Error('Open a sequence with prim-plan on V1 and synced general footage on V2.');
  var settings=s.getSettings(), upper=[];
  for (var i=2;i<s.videoTracks.numTracks;i++) upper=upper.concat(EP.clips(s.videoTracks[i],false));
  return {project:app.project.path, sequenceID:String(s.sequenceID), name:s.name, mediaPath:media,
    timebase:String(s.timebase), zeroPoint:String(s.zeroPoint), width:settings.videoFrameWidth,
    height:settings.videoFrameHeight, videoDisplayFormat:settings.videoDisplayFormat,
    v1:EP.clips(s.videoTracks[0],true), v2:EP.clips(s.videoTracks[1],true),
    upper:upper, audio:EP.audio(s), videoTracks:s.videoTracks.numTracks};
};
function ACP_episodeSnapshot(payload) { return EP.reply(function(p) {
  var media=p.mediaPath;
  if (!media) {
    var selection=app.getCurrentProjectViewSelection();
    if (!selection || selection.length !== 1) throw Error('Select the prim-plan source in the Project panel.');
    media=selection[0].getMediaPath();
  }
  var s=p.sequenceID ? EP.sequence(p.sequenceID) : app.project.activeSequence;
  return {snapshot:EP.snapshot(s,media), ok:'Timeline captured'};
}, payload); }
function ACP_episodeSeek(payload) { return EP.reply(function(p) {
  var s=EP.sequence(p.sequenceID); app.project.openSequence(s.sequenceID); s.setPlayerPosition(p.ticks);
  return {ok:'Play the full proposed interval in Premiere'};
}, payload); }
EP.clone = function (source, name) {
  var ids={}, i, found=[];
  for (i=0;i<app.project.sequences.numSequences;i++) ids[String(app.project.sequences[i].sequenceID)]=true;
  if (!source.clone()) throw Error('Premiere could not clone the sequence');
  for (i=0;i<app.project.sequences.numSequences;i++) if (!ids[String(app.project.sequences[i].sequenceID)]) found.push(app.project.sequences[i]);
  if (found.length !== 1) throw Error('Could not identify exactly one new sequence');
  found[0].name=name; app.project.openSequence(found[0].sequenceID); return found[0];
};
EP.lumetri = function (clip) {
  var found=[];
  for(var i=0;i<clip.components.numItems;i++) if(/^(AE\.)?ADBE Lumetri$/.test(clip.components[i].matchName)) found.push(clip.components[i]);
  if(found.length>1) throw Error('Multiple Lumetri instances. Keep one intended grade on V2.');
  return found.length ? found[0] : null;
};
EP.lumetriValues = function (clip) {
  var comp=EP.lumetri(clip); if(!comp) throw Error('Add Lumetri Color to the first V2 clip in the calibration copy.');
  var props=EP.props(comp);
  for(var i=0;i<props.length;i++) if(props[i].animated) throw Error('Animated Lumetri cannot be calibrated.');
  return props;
};
function ACP_episodeCalibration(payload) { return EP.reply(function(p) {
  if(p.action==='clone') {
    var source=EP.sequence(p.sequenceID), copy=EP.clone(source,source.name+' - Full Episode COLOR CHECK');
    return {sequenceID:String(copy.sequenceID),version:app.version,ok:'Calibration copy created. Set Lumetri on its first V2 clip.'};
  }
  var s=EP.sequence(p.sequenceID);
  if(s.name.indexOf(' - Full Episode COLOR CHECK')<0) throw Error('Calibration is limited to the named scratch copy.');
  return {values:EP.lumetriValues(s.videoTracks[1].clips[0]),version:app.version,ok:'Color values captured'};
}, payload); }
function ACP_episodeCapabilities(payload) { return EP.reply(function(p) {
  app.enableQE(); var s=app.project.activeSequence;
  if(!s) throw Error('Open the source sequence first.');
  var q=qe.project.getActiveSequence();
  if(!q) throw Error('Premiere QE is unavailable.');
  return {version:app.version, ok:'Premiere bridge available. QE/color behavior still requires a live test.',
    hasV2:s.videoTracks.numTracks>1};
}, payload); }
EP.qclip = function (track, clip) {
  for(var i=0;i<track.numItems;i++) {
    var item=track.getItemAt(i);
    if(item && item.type==='Clip' && item.start && String(item.start.ticks)===String(clip.start.ticks)) return item;
  }
  throw Error('QE could not identify the V2 clip at '+clip.start.ticks);
};
EP.grade = function (clip, qtrack, calibration, blacks) {
  var comp=EP.lumetri(clip);
  if(!comp) {
    var fx=qe.project.getVideoEffectByName('Lumetri Color');
    if(!fx) throw Error('Lumetri Color is unavailable in this Premiere language/version.');
    EP.qclip(qtrack,clip).addVideoEffect(fx); comp=EP.lumetri(clip);
  }
  if(!comp) throw Error('Could not add Lumetri Color');
  var props=EP.props(comp), names=[];
  for(var i=0;i<props.length;i++) {
    names.push(props[i].name);
    if(props[i].animated) throw Error('Animated Lumetri requires manual grading.');
  }
  if(EP.stringify(names)!==EP.stringify(calibration.names)) throw Error('Lumetri controls changed. Recalibrate.');
  function set(index,value) {
    var prop=comp.properties[index]; prop.setValue(value,true);
    if(Math.abs(Number(prop.getValue())-value)>0.00001) throw Error('Lumetri value did not persist');
  }
  set(calibration.saturationIndex,calibration.saturation114);
  if(blacks!==null) set(calibration.blacksIndex,calibration.blackZero+blacks*calibration.blackSlope);
};
EP.stripIDs = function (value) {
  if(value instanceof Array) { var arr=[]; for(var i=0;i<value.length;i++) arr.push(EP.stripIDs(value[i])); return arr; }
  if(value && typeof value==='object') {
    var out={}; for(var k in value) if(value.hasOwnProperty(k) && k!=='id') out[k]=EP.stripIDs(value[k]); return out;
  }
  return value;
};
EP.same = function(a,b) { return EP.stringify(EP.stripIDs(a))===EP.stringify(EP.stripIDs(b)); };
EP.verify = function(copy,plan,overlayIndex) {
  var dimensions=copy.getSettings();
  if(String(copy.timebase)!==plan.snapshot.timebase || String(copy.zeroPoint)!==plan.snapshot.zeroPoint || dimensions.videoFrameWidth!==plan.snapshot.width || dimensions.videoFrameHeight!==plan.snapshot.height) throw Error('Sequence format or display start changed');
  if(!EP.same(EP.clips(copy.videoTracks[0],true),plan.snapshot.v1) || !EP.same(EP.audio(copy),plan.snapshot.audio)) throw Error('V1/audio invariant failed. Inspect the incomplete copy.');
  var actual=EP.clips(copy.videoTracks[1],true), general=[],overlays=[],i,j,e;
  for(i=0;i<plan.events.length;i++) {e=plan.events[i];(e.kind==='general'?general:overlays).push(e);}
  var enabled=[];
  for(i=0;i<actual.length;i++) {
    var c=actual[i], original=null;
    for(j=0;j<plan.snapshot.v2.length;j++) {
      var old=plan.snapshot.v2[j];
      if(c.itemID===old.itemID && EP.inside(c.start,c.end,old.start,old.end)) original=old;
    }
    if(!original || c.sourceIn!==EP.addTicks(original.sourceIn,EP.subtractTicks(c.start,original.start)) || c.sourceOut!==EP.addTicks(original.sourceIn,EP.subtractTicks(c.end,original.start))) throw Error('V2 source synchronization changed');
    function framing(effects) {var out=[];for(var k=0;k<effects.length;k++)if(!/^(AE\.)?ADBE Lumetri$/.test(effects[k].matchName))out.push(effects[k]);return out;}
    if(!EP.same(framing(c.effects),framing(original.effects))) throw Error('V2 framing/effects changed');
    if(!c.disabled) enabled.push(c);
  }
  for(i=0;i<general.length;i++) {
    var cursor=general[i].start;
    for(j=0;j<enabled.length;j++) if(EP.inside(enabled[j].start,enabled[j].end,general[i].start,general[i].end)) {
      if(enabled[j].start!==cursor) throw Error('General-camera coverage has a gap'); cursor=enabled[j].end;
    }
    if(cursor!==general[i].end) throw Error('General-camera coverage is incomplete');
  }
  for(i=0;i<enabled.length;i++) {
    var covered=false;
    for(j=0;j<general.length;j++)if(EP.inside(enabled[i].start,enabled[i].end,general[j].start,general[j].end))covered=true;
    if(!covered)throw Error('Unexpected enabled V2 material');
  }
  var upper=[];
  for(i=2;i<plan.snapshot.videoTracks;i++)upper=upper.concat(EP.clips(copy.videoTracks[i],false));
  if(!EP.same(upper,plan.snapshot.upper))throw Error('Existing upper picture tracks changed');
  if(overlays.length) {
    var placed=EP.clips(copy.videoTracks[overlayIndex],false);
    if(placed.length!==overlays.length)throw Error('Overlay count differs from the reviewed plan');
    for(i=0;i<overlays.length;i++) {
      var match=false;
      for(j=0;j<placed.length;j++)if(placed[j].start===overlays[i].start && placed[j].end===overlays[i].end && placed[j].path===overlays[i].asset.path && !placed[j].disabled)match=true;
      if(!match)throw Error('Overlay timing/media differs from plan: '+overlays[i].id);
    }
  }
  if(String(copy.getInPointAsTime().ticks)!==plan.bounds[0] || String(copy.getOutPointAsTime().ticks)!==plan.bounds[1]) throw Error('Sequence delivery range did not persist');
  return {ok:'Duplicate timeline invariants verified',sequenceID:String(copy.sequenceID)};
};
function ACP_episodeVerify(payload) { return EP.reply(function(p) {
  var plan=EP.read(p.path);return EP.verify(EP.sequence(p.sequenceID),plan,plan.snapshot.videoTracks);
},payload); }
function ACP_episodeBuild(payload) { return EP.reply(function(p) {
  var plan=EP.read(p.path), source=EP.sequence(plan.snapshot.sequenceID), copy=null, savedZero=null;
  if(!plan.validated) throw Error('Validate the reviewed plan first.');
  if(EP.stringify(EP.snapshot(source,plan.snapshot.mediaPath))!==EP.stringify(plan.snapshot)) throw Error('Timeline changed. Prepare again.');
  var general=[], overlays=[], i, e, cal=p.calibration;
  for(i=0;i<plan.events.length;i++) { e=plan.events[i]; (e.kind==='general'?general:overlays).push(e); }
  if(general.length && (!cal || cal.version!==app.version)) throw Error('Complete the one-time Lumetri calibration for this Premiere version first.');
  try {
    copy=EP.clone(source,source.name+' - Full Episode - '+plan.config.show+' - '+new Date().getTime());
    app.enableQE(); var q=qe.project.getActiveSequence(), track=copy.videoTracks[1];
    savedZero=String(copy.zeroPoint); copy.setZeroPoint('0');
    var boundary={}, settings=copy.getSettings();
    for(i=0;i<general.length;i++) { boundary[general[i].start]=true; boundary[general[i].end]=true; }
    for(var tick in boundary) if(boundary.hasOwnProperty(tick)) {
      var needsCut=false;
      for(var c=0;c<track.clips.numItems;c++) if(EP.compare(tick,track.clips[c].start.ticks)>0 && EP.compare(tick,track.clips[c].end.ticks)<0) needsCut=true;
      if(needsCut) q.getVideoTrackAt(1).razor(EP.time(tick).getFormatted(EP.time(copy.timebase),settings.videoDisplayFormat));
    }
    for(i=0;i<track.clips.numItems;i++) {
      var clip=track.clips[i], keep=false;
      for(c=0;c<general.length;c++) {
        e=general[c];
        if(EP.inside(clip.start.ticks,clip.end.ticks,e.start,e.end)) keep=true;
        else if(EP.compare(clip.start.ticks,e.end)<0 && EP.compare(e.start,clip.end.ticks)<0) throw Error('V2 razor did not create the requested boundaries.');
      }
      clip.disabled=!keep;
      if(keep) EP.grade(clip,q.getVideoTrackAt(1),cal,plan.config.show==='AIP'?Number(plan.config.blacks):null);
    }
    copy.setZeroPoint(savedZero); savedZero=null;
    var overlayIndex=copy.videoTracks.numTracks;
    if(overlays.length && !ACP_ensureTracks(copy,overlayIndex+1,copy.audioTracks.numTracks)) throw Error('Cannot create an overlay track');
    for(i=0;i<overlays.length;i++) {
      e=overlays[i]; var asset=ACP_importAndFind(e.asset.path);
      if(!asset) throw Error('Cannot import '+e.asset.path);
      var sub=asset.createSubClip('Full Episode '+e.id,'0',e.asset.duration,1,1,0);
      if(!sub) throw Error('Cannot make a bounded video-only animation subclip');
      copy.videoTracks[overlayIndex].overwriteClip(sub,EP.time(e.start).seconds);
      var inserted=null, clips=copy.videoTracks[overlayIndex].clips;
      for(c=0;c<clips.numItems;c++) if(String(clips[c].start.ticks)===e.start) inserted=clips[c];
      if(!inserted) throw Error('Overlay insertion failed');
      // Premiere decides rate conversion; never silently truncate or extend an animation.
      if(String(inserted.end.ticks)!==e.end) throw Error('Asset duration/frame-rate conversion differs from plan: '+e.id);
      var motion=null;
      for(c=0;c<inserted.components.numItems;c++) if(inserted.components[c].matchName==='AE.ADBE Motion') motion=inserted.components[c];
      if(!motion) throw Error('Motion effect unavailable');
      var scaled=false, positioned=false;
      for(c=0;c<motion.properties.numItems;c++) {
        var prop=motion.properties[c];
        if(prop.displayName==='Scale') { prop.setValue(e.scale,true); scaled=Math.abs(Number(prop.getValue())-e.scale)<.0001; }
        if(prop.displayName==='Position') { prop.setValue(e.position,true); positioned=EP.stringify(prop.getValue())===EP.stringify(e.position); }
      }
      if(!scaled || !positioned) throw Error('Cannot verify native animation scale/position');
    }
    copy.setInPoint(EP.time(plan.bounds[0]).seconds); copy.setOutPoint(EP.time(plan.bounds[1]).seconds);
    EP.verify(copy,plan,overlayIndex);
    if(EP.stringify(EP.snapshot(source,plan.snapshot.mediaPath))!==EP.stringify(plan.snapshot)) throw Error('Original sequence invariant failed');
    return {ok:'Full Episode duplicate built. Export using Sequence In/Out.',sequenceID:String(copy.sequenceID),name:copy.name,bounds:plan.bounds};
  } catch(error) {
    if(copy) { copy.name='INCOMPLETE - '+copy.name; if(savedZero!==null) copy.setZeroPoint(savedZero); }
    app.project.openSequence(source.sequenceID); throw error;
  }
}, payload); }
