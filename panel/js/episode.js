/* Full Episode is deliberately isolated from the existing reels state machine. */
(function () {
  "use strict";
  var $ = function (id) { return document.getElementById(id); };
  var cs = new CSInterface(), req = window.cep_node && window.cep_node.require;
  if (!req) { $("epStatus").textContent = "Full Episode needs the panel's Node runtime."; return; }
  var fs = req("fs"), path = req("path"), cp = req("child_process"), url = req("url");
  var settings, root, profiles = {}, snapshot = null, analysis = null, plan = null, jobDir = null;
  var busy = false, process = null, references = [], calibration = null, colorCopy = null, colorSamples = [];
  var TPS = 254016000000n;
  var kinds = {host: "Host animation", sponsor: "Paralela 45", subscribe: "Like & Subscribe"};
  function status(message, cls) { $("epStatus").textContent = message; $("epStatus").className = "hint " + (cls || ""); }
  function log(message) { $("log").textContent += message + "\n"; }
  function read(file) { return JSON.parse(fs.readFileSync(file, "utf8")); }
  function write(file, data) { fs.mkdirSync(path.dirname(file), {recursive:true}); fs.writeFileSync(file+".tmp", JSON.stringify(data,null,2)); fs.renameSync(file+".tmp",file); }
  function fileURL(file) { return url.pathToFileURL(file).href; }
  function seconds(t) { return (Number(t) / Number(TPS)).toFixed(2); }
  function toTicks(value) {
    if (!/^\d+(\.\d{1,6})?$/.test(String(value))) throw Error("Enter non-negative timeline seconds (up to six decimal places).");
    var parts = String(value).split(".");
    return BigInt(parts[0])*TPS + BigInt((parts[1] || "").padEnd(6,"0"))*TPS/1000000n;
  }
  function el(tag, text, cls) { var node=document.createElement(tag); if(text!==undefined) node.textContent=text; if(cls) node.className=cls; return node; }
  function button(text, fn) { var b=el("button",text,"ghost"); b.type="button"; b.onclick=fn; return b; }
  function guard(fn) { return function () { Promise.resolve().then(fn).catch(function(e) { status(e.message,"err"); log(e.stack || e.message); }); }; }
  function host(name, data) {
    return new Promise(function(resolve,reject) {
      cs.evalScript(name+"("+JSON.stringify(JSON.stringify(data || {}))+")",function(raw) {
        try { var result=JSON.parse(raw); if(result.error) reject(Error(result.error)); else resolve(result); }
        catch(e) { reject(Error("Unexpected Premiere reply: "+raw)); }
      });
    });
  }
  function setBusy(value) {
    busy=value; $("epSettings").disabled=value;
    ["epAnalyze","epPlan","epBuild","epPropose"].forEach(function(id) { $(id).disabled=value; });
    $("epAnalyze").disabled=value || !snapshot;
    $("epPlan").disabled=value || !analysis;
    $("epCancel").hidden=!value || !process;
    $("epShots").querySelectorAll("input,select,button").forEach(function(n){n.disabled=value;});
    $("epEvents").querySelectorAll("input,button").forEach(function(n){n.disabled=value;});
    $("epSpeaker").disabled=value;
  }
  function current() {
    var config={show:$("epShow").value,general:$("epGeneral").checked,blacks:Number($("epBlacks").value),
      overlaysOff:$("epAllOff").checked,assets:{},positions:{},hostSpeaker:$("epSpeaker").value || null,
      openingSponsor:$("epCue").value === "" ? null : Number($("epCue").value)};
    Object.keys(kinds).forEach(function(k) {
      config[k]=$("ep-"+k+"-on").checked;
      config.assets[k]=$("ep-"+k+"-file").value.trim();
      config.positions[k]=[Number($("ep-"+k+"-x").value)/100,Number($("ep-"+k+"-y").value)/100];
    });
    return config;
  }
  function saveProfile() {
    profiles[$("epShow").value]=current();
    write(path.join(root,"profiles.json"),{shows:profiles,calibration:calibration,lastJob:jobDir});
  }
  function loadProfile() {
    var saved=profiles[$("epShow").value] || {};
    $("epGeneral").checked=saved.general!==false; $("epBlacks").value=saved.blacks || 0;
    $("epAllOff").checked=!!saved.overlaysOff;
    Object.keys(kinds).forEach(function(k) {
      $("ep-"+k+"-on").checked=saved[k]!==false;
      $("ep-"+k+"-file").value=(saved.assets || {})[k] || "";
      var pos=(saved.positions || {})[k] || [.5,.5];
      $("ep-"+k+"-x").value=pos[0]*100; $("ep-"+k+"-y").value=pos[1]*100;
    });
    $("epCue").value=""; updateShow();
  }
  function updateShow() {
    var cpi=$("epShow").value==="CPI";
    $("epBlacksField").hidden=cpi; $("epBlacksValue").textContent=$("epBlacks").value;
    $("ep-sponsor-group").hidden=!cpi; $("epCueField").hidden=!cpi || $("epAllOff").checked || !$("ep-sponsor-on").checked;
    $("epGraphics").hidden=$("epAllOff").checked;
  }
  function invalidate() { plan=null; $("epReview").hidden=true; $("epResult").textContent=""; }
  function persistPlan() { if(plan && jobDir)write(path.join(jobDir,"plan.json"),plan); }
  function analysisSettings(c) { return JSON.stringify([c.show,c.overlaysOff,c.host,c.sponsor,c.subscribe,c.assets]); }
  function job() { return {snapshot:snapshot,config:current(),cache:path.join(root,"sources"),speechPython:settings.python,references:references}; }
  function run(action) {
    if(busy) return Promise.reject(Error("Wait for the current operation."));
    var sourceJob=job();
    write(path.join(jobDir,"job.json"),sourceJob);
    var cancel=path.join(jobDir,"cancel"); if(fs.existsSync(cancel)) fs.unlinkSync(cancel);
    var videoPython=path.join(settings.engineDir,"..",".episode-venv","Scripts","python.exe");
    var needsVideo=action==="propose" || (action==="analyze" && !sourceJob.config.overlaysOff &&
      (sourceJob.config.host || sourceJob.config.show==="CPI" && sourceJob.config.sponsor));
    if(needsVideo && !fs.existsSync(videoPython)) return Promise.reject(Error("Run setup-episode.ps1 once to install the isolated video tools, then reopen the panel. Existing reels dependencies are unchanged."));
    return new Promise(function(resolve,reject) {
      var error=null, tail="";
      process=cp.spawn(needsVideo ? videoPython : settings.python,[path.join(settings.engineDir,"episode.py"),action,path.join(jobDir,"job.json")],{windowsHide:true});
      setBusy(true); status("Working. Completed source analysis is cached.");
      function output(data) { var text=data.toString("utf8"); log(text); tail=(tail+text).slice(-3000); var lines=tail.trim().split(/\r?\n/); status(lines[lines.length-1]); }
      process.stdout.on("data",output); process.stderr.on("data",output);
      process.on("error",function(e){error=e;});
      process.on("close",function(code) {
        process=null; setBusy(false);
        if(error) { reject(error); return; }
        var result;
        try { result=read(path.join(jobDir,"worker-result.json")); }
        catch(e) { reject(Error("Worker ended without a result. "+tail)); return; }
        if(code!==0 || result.error) reject(Error(result.error || tail));
        else { status(result.ok,"ok"); resolve(result); }
      });
    });
  }
  function seek(tick) { return host("ACP_episodeSeek",{sequenceID:snapshot.sequenceID,ticks:String(tick)}); }
  function preview(event) {
    var sources=event.sources, index=0, video=$("epSourceVideo"), overlay=$("epOverlayVideo");
    if(!sources || !sources.length)throw Error("This placement has no mapped source frames.");
    $("epPlayback").hidden=false;$("epPlaybackTitle").textContent=event.kind==="general"?"General camera preview":(kinds[event.kind] || "Host camera preview");
    overlay.pause();overlay.hidden=!event.asset;
    if(event.asset){overlay.src=fileURL(event.asset.path);overlay.style.width=(event.asset.width/snapshot.width*event.scale)+"%";overlay.style.left=event.position[0]*100+"%";overlay.style.top=event.position[1]*100+"%";}
    function load(){video.onloadedmetadata=function(){video.currentTime=Number(sources[index].sourceIn)/Number(TPS);video.play().catch(function(e){status("Press Play to review this source. "+e.message);});};video.src=fileURL(sources[index].path);}
    video.onerror=function(){status("This codec cannot play inside the panel. Use Play in Premiere and view V1 for host checks.","err");};
    video.onplay=function(){if(event.asset)overlay.play().catch(function(){});};video.onpause=function(){overlay.pause();};
    video.ontimeupdate=function(){
      var source=sources[index];
      if(video.currentTime>=Number(source.sourceOut)/Number(TPS)){
        if(index+1<sources.length){index++;load();}else{video.pause();overlay.pause();}return;
      }
      if(event.asset){var elapsed=video.currentTime-Number(source.sourceIn)/Number(TPS)+(Number(source.start)-Number(event.start))/Number(TPS);if(Math.abs(overlay.currentTime-elapsed)>.15)overlay.currentTime=Math.max(0,elapsed);}
    };
    load();$("epPlayback").scrollIntoView({block:"nearest"});
  }
  function persistAnalysis() { write(path.join(jobDir,"analysis.json"),analysis); invalidate(); }
  function showShots() {
    var container=$("epShots"); container.textContent="";
    var seen={};
    analysis.shots.forEach(function(shot) {
      if(seen[shot.id]) return; seen[shot.id]=true;
      var box=el("article",undefined,"episode-shot"), canvas=el("canvas"), image=new Image(), rect=null, drag=null;
      image.onload=function(){canvas.width=image.naturalWidth;canvas.height=image.naturalHeight;draw();};
      image.src=fileURL(shot.thumbnail);
      function draw() {
        var ctx=canvas.getContext("2d");ctx.drawImage(image,0,0);
        if(rect){ctx.strokeStyle="#D5A13C";ctx.lineWidth=3;ctx.strokeRect(rect[0]*canvas.width,rect[1]*canvas.height,rect[2]*canvas.width,rect[3]*canvas.height);}
      }
      function point(e){var r=canvas.getBoundingClientRect();return [Math.max(0,Math.min(1,(e.clientX-r.left)/r.width)),Math.max(0,Math.min(1,(e.clientY-r.top)/r.height))];}
      canvas.onpointerdown=function(e){if(busy)return;drag=point(e);canvas.setPointerCapture(e.pointerId);};
      canvas.onpointerup=function(e){
        if(!drag)return;var p=point(e);rect=[Math.min(drag[0],p[0]),Math.min(drag[1],p[1]),Math.abs(p[0]-drag[0]),Math.abs(p[1]-drag[1])];drag=null;
        references=references.filter(function(r){return r.id!==(shot.sourceID || shot.id);});
        if(rect[2]>.02 && rect[3]>.02) references.push({id:shot.sourceID || shot.id,roi:rect,backgroundOnly:false}); else rect=null;
        draw();$("epBackgroundOnly").checked=false;
      };
      box.appendChild(canvas);
      box.appendChild(el("p",seconds(shot.start)+"–"+seconds(shot.end)+"s"+(shot.proposedHost ? " · similar background" : "")));
      var select=el("select");select.setAttribute("aria-label","Camera view at "+seconds(shot.start)+" seconds");
      [["","Unlabelled"],["host","Host visible throughout"],["other","Guest / uncertain"]].forEach(function(pair){var o=el("option",pair[1]);o.value=pair[0];select.appendChild(o);});
      select.value=shot.host===true?"host":shot.host===false?"other":"";
      select.onchange=function(){analysis.shots.forEach(function(s){if(s.id===shot.id)s.host=select.value==="host"?true:select.value==="other"?false:null;});persistAnalysis();};
      box.appendChild(select);box.appendChild(button("Play shot",guard(function(){
        var pieces=analysis.shots.filter(function(s){return s.id===shot.id;});var start=pieces.reduce(function(a,s){return BigInt(s.start)<a?BigInt(s.start):a;},BigInt(shot.start));var end=pieces.reduce(function(a,s){return BigInt(s.end)>a?BigInt(s.end):a;},BigInt(shot.end));
        var sources=snapshot.v1.filter(function(c){return BigInt(c.start)<end && start<BigInt(c.end);}).map(function(c){var a=start>BigInt(c.start)?start:BigInt(c.start),b=end<BigInt(c.end)?end:BigInt(c.end),offset=BigInt(c.sourceIn)-BigInt(c.start);return {path:c.path,start:String(a),end:String(b),sourceIn:String(a+offset),sourceOut:String(b+offset)};});
        preview({kind:"shot",start:String(start),sources:sources});return seek(start);
      })));container.appendChild(box);
    });
    $("epLabels").hidden=!analysis.shots.length;
  }
  function showAnalysis() {
    var selected=$("epSpeaker").value;
    $("epSpeaker").textContent="";var option=el("option","Choose the host");option.value="";$("epSpeaker").appendChild(option);
    analysis.speakers.forEach(function(s){var o=el("option","Speaker "+s);o.value=String(s);$("epSpeaker").appendChild(o);});
    $("epSpeaker").value=selected;
    showShots();
    var previews=$("epAssetPreviews");previews.textContent="";
    Object.keys(analysis.assets).forEach(function(k){
      var asset=analysis.assets[k], box=el("div",undefined,"episode-asset");
      box.appendChild(el("p",kinds[k]+" · "+seconds(asset.duration)+"s"+(asset.hasAudio?" · embedded audio will be omitted":"")));
      var preview=el("img");preview.src=fileURL(asset.preview);preview.className="episode-alpha";preview.alt=kinds[k]+" transparency preview";box.appendChild(preview);
      var video=el("video");video.src=fileURL(asset.path);video.controls=true;video.muted=true;video.preload="metadata";video.className="episode-alpha";box.appendChild(video);
      previews.appendChild(box);
    });
    $("epAssetReview").hidden=!Object.keys(analysis.assets).length;$("epPlan").disabled=false;
  }
  function showPlan() {
    $("epReview").hidden=false;$("epWarnings").textContent=plan.unresolved.concat(plan.warnings).join("\n");
    $("epReducedLabel").hidden=plan.config.show!=="CPI" || !plan.config.sponsor || plan.config.overlaysOff;
    $("epReduced").checked=!!plan.reducedSponsorAcknowledged;
    var list=$("epEvents");list.textContent="";
    plan.events.forEach(function(event){
      var row=el("article",undefined,"episode-event"), on=el("input"), reviewed=el("input");on.type="checkbox";on.checked=event.enabled;on.setAttribute("aria-label","Include "+event.id);
      on.onchange=function(){event.enabled=on.checked;event.reviewed=false;reviewed.checked=false;persistPlan();};row.appendChild(on);
      row.appendChild(el("strong",event.kind==="general"?"General camera":kinds[event.kind]));
      row.appendChild(button("Play",guard(function(){preview(event);return seek(event.start);})));var time=el("div",undefined,"episode-event-time"), input=el("input");
      input.type="number";input.min="0";input.step="0.01";input.value=seconds(event.start);input.setAttribute("aria-label",event.id+" start seconds");
      input.onchange=guard(function(){
        var f=BigInt(snapshot.timebase), t=toTicks(input.value)/f*f, d=BigInt(event.end)-BigInt(event.start);event.start=String(t);event.end=String(t+d);event.reviewed=false;reviewed.checked=false;input.value=seconds(t);
        event.sources=snapshot[event.kind==="general"?"v2":"v1"].filter(function(c){return BigInt(c.start)<t+d && t<BigInt(c.end);}).map(function(c){var a=t>BigInt(c.start)?t:BigInt(c.start),b=t+d<BigInt(c.end)?t+d:BigInt(c.end),offset=BigInt(c.sourceIn)-BigInt(c.start);return {path:c.path,start:String(a),end:String(b),sourceIn:String(a+offset),sourceOut:String(b+offset)};});
        persistPlan();
      });
      time.appendChild(input);time.appendChild(el("span","s · "+seconds(BigInt(event.end)-BigInt(event.start))+"s long"));
      var label=el("label");reviewed.type="checkbox";reviewed.checked=event.reviewed;reviewed.onchange=function(){event.reviewed=reviewed.checked;persistPlan();};label.appendChild(reviewed);label.appendChild(document.createTextNode("Reviewed full interval"));time.appendChild(label);row.appendChild(time);
      row.appendChild(el("p",event.reason+(event.evidence?" · "+event.evidence:""),"episode-evidence"));list.appendChild(row);
    });
  }
  Object.keys(kinds).forEach(function(k){
    var group=el("div",undefined,"episode-asset");group.id="ep-"+k+"-group";
    var label=el("label"), check=el("input");check.type="checkbox";check.id="ep-"+k+"-on";check.checked=true;label.appendChild(check);label.appendChild(document.createTextNode(kinds[k]));group.appendChild(label);
    var row=el("div",undefined,"row"), input=el("input");input.id="ep-"+k+"-file";input.type="text";input.placeholder="Choose a video with transparency";input.setAttribute("aria-label",kinds[k]+" video");row.appendChild(input);
    row.appendChild(button("Browse",guard(function(){var result=window.cep.fs.showOpenDialog(false,false,"Choose "+kinds[k],null,null);if(result.data&&result.data[0]){input.value=result.data[0];saveProfile();invalidate();}})));group.appendChild(row);
    var position=el("div",undefined,"episode-position");["x","y"].forEach(function(axis){var l=el("label",axis==="x"?"Horizontal position (%)":"Vertical position (%)"),n=el("input");n.type="number";n.min="0";n.max="100";n.step="1";n.value="50";n.id="ep-"+k+"-"+axis;l.appendChild(n);position.appendChild(l);});group.appendChild(position);$("epGraphics").appendChild(group);
  });
  $("workflowReels").onclick=function(){switchWorkflow(false);};$("workflowEpisode").onclick=function(){switchWorkflow(true);};
  function switchWorkflow(episode){$("reelsWorkflow").hidden=episode;$("episodeWorkflow").hidden=!episode;["Reels","Episode"].forEach(function(k){var active=(k==="Episode")===episode;$("workflow"+k).classList.toggle("current",active);$("workflow"+k).setAttribute("aria-pressed",String(active));});}
  $("epShow").onchange=function(){loadProfile();invalidate();};
  $("epSettings").addEventListener("change",guard(function(){updateShow();saveProfile();invalidate();}));
  $("epBlacks").oninput=function(){$("epBlacksValue").textContent=this.value;};
  $("epCapture").onclick=guard(async function(){var r=await host("ACP_episodeSnapshot",{});snapshot=r.snapshot;analysis=null;invalidate();$("epLabels").hidden=true;$("epAssetReview").hidden=true;$("epPlan").disabled=true;$("epTimeline").textContent=snapshot.name+" · "+snapshot.v1.length+" V1 clips · "+snapshot.v2.length+" V2 clips";$("epAnalyze").disabled=false;status("Timeline captured. Choose enabled graphics, then analyze.","ok");});
  var analyzedSettings=null;
  $("epAnalyze").onclick=guard(async function(){
    if(!snapshot)throw Error("Read the timeline first.");
    var fresh=(await host("ACP_episodeSnapshot",{sequenceID:snapshot.sequenceID,mediaPath:snapshot.mediaPath})).snapshot;
    snapshot=fresh;jobDir=path.join(root,"jobs",String(Date.now()));references=[];analysis=null;invalidate();
    $("epLabels").hidden=true;$("epAssetReview").hidden=true;$("epPlan").disabled=true;
    analyzedSettings=analysisSettings(current());saveProfile();await run("analyze");analysis=read(path.join(jobDir,"analysis.json"));showAnalysis();
  });
  $("epCancel").onclick=function(){if(jobDir){fs.writeFileSync(path.join(jobDir,"cancel"),"cancel");status("Cancelling after the current decode/probe operation…");}};
  $("epPropose").onclick=guard(async function(){
    if(!references.length || !$("epBackgroundOnly").checked)throw Error("Select a background rectangle and confirm it excludes people.");
    references.forEach(function(r){r.backgroundOnly=true;});await run("propose");analysis=read(path.join(jobDir,"analysis.json"));showShots();
  });
  $("epSpeakerSeek").onclick=guard(async function(){var speaker=$("epSpeaker").value;var turn=analysis && analysis.turns.find(function(t){return String(t.speaker)===speaker;});if(!turn)throw Error("Choose a speaker with a speech sample.");await seek(turn.start);});
  $("epSpeaker").onchange=invalidate;
  $("epReduced").onchange=function(){if(plan){plan.reducedSponsorAcknowledged=this.checked;persistPlan();}};
  $("epPlan").onclick=guard(async function(){
    if(analysisSettings(current())!==analyzedSettings)throw Error("Graphic choices changed. Analyze again to inspect their assets.");
    await run("plan");plan=read(path.join(jobDir,"plan.json"));showPlan();
  });
  $("epBuild").onclick=guard(async function(){
    if(!plan)throw Error("Create placements first.");plan.reducedSponsorAcknowledged=$("epReduced").checked;write(path.join(jobDir,"plan.json"),plan);
    await run("validate");setBusy(true);
    try {
      status("Building a separate Full Episode sequence…");
      var r=await host("ACP_episodeBuild",{path:path.join(jobDir,"build.json"),calibration:calibration});
      write(path.join(jobDir,"build-result.json"),r);$("epResult").textContent=r.name+". Export with Sequence In/Out. Inspect and play the duplicate before delivery.";status(r.ok,"ok");
    } finally {setBusy(false);}
  });
  $("epColorClone").onclick=guard(async function(){
    if(!snapshot)throw Error("Read the source timeline first.");var r=await host("ACP_episodeCalibration",{action:"clone",sequenceID:snapshot.sequenceID});colorCopy=r.sequenceID;colorSamples=[];$("epColorCapture").disabled=false;$("epColorStatus").textContent=r.ok;$("epColorHelp").textContent="On the copy's first V2 clip, add Lumetri Color. Set Basic Correction Saturation 100 and Blacks 0, then capture.";
  });
  $("epColorCapture").onclick=guard(async function(){
    var r=await host("ACP_episodeCalibration",{action:"read",sequenceID:colorCopy});colorSamples.push(r.values);
    if(colorSamples.length===1){$("epColorHelp").textContent="Set Basic Correction Saturation 114 and Blacks +20 on the same clip, then capture.";return;}
    if(colorSamples.length===2){$("epColorHelp").textContent="Keep Saturation 114. Set Blacks −20 on the same clip, then capture.";return;}
    var a=colorSamples[0],b=colorSamples[1],c=colorSamples[2],si=[],bi=[];
    if(a.length!==b.length || b.length!==c.length)throw Error("Lumetri changed during calibration. Start a new calibration copy.");
    a.forEach(function(p,i){
      if(p.name!==b[i].name || p.name!==c[i].name)throw Error("Lumetri property order changed.");
      if(typeof p.value!=="number")return;
      if(p.value!==b[i].value && b[i].value===c[i].value)si.push(i);
      if(b[i].value!==c[i].value && Math.abs((b[i].value+c[i].value)/2-p.value)<.00001)bi.push(i);
    });
    if(si.length!==1 || bi.length!==1 || !/saturation/i.test(a[si[0]].name) || !/black/i.test(a[bi[0]].name)){
      colorSamples=[];throw Error("Could not identify unique Basic Saturation and Blacks controls. Use English effect labels and restart calibration.");
    }
    calibration={version:r.version,names:a.map(function(p){return p.name;}),saturationIndex:si[0],saturation114:b[si[0]].value,blacksIndex:bi[0],blackZero:a[bi[0]].value,blackSlope:(b[bi[0]].value-c[bi[0]].value)/40};
    saveProfile();$("epColorCapture").disabled=true;$("epColorStatus").textContent="Color mapping saved for Premiere "+r.version+". Calibration copy retained for inspection.";status("Color mapping captured. Live build/render verification remains separate.","ok");
  });
  try {
    settings=read(path.join(cs.getExtensionPath(),"config.json"));root=path.join(settings.cacheDir,"full_episode");
    var profilePath=path.join(root,"profiles.json");
    if(fs.existsSync(profilePath)){var stored=read(profilePath);profiles=stored.shows || {};calibration=stored.calibration || null;}
    loadProfile();
    if(calibration)$("epColorStatus").textContent="Saved calibration: Premiere "+calibration.version;
    if(stored && stored.lastJob && fs.existsSync(path.join(stored.lastJob,"analysis.json"))){
      $("epTimeline").appendChild(button("Resume last episode",guard(async function(){
        var old=read(path.join(stored.lastJob,"job.json"));
        var fresh=(await host("ACP_episodeSnapshot",{sequenceID:old.snapshot.sequenceID,mediaPath:old.snapshot.mediaPath})).snapshot;
        if(JSON.stringify(fresh)!==JSON.stringify(old.snapshot))throw Error("The saved timeline changed. Read it and analyze again.");
        jobDir=stored.lastJob;snapshot=fresh;profiles[old.config.show]=old.config;$("epShow").value=old.config.show;loadProfile();$("epCue").value=old.config.openingSponsor===null?"":old.config.openingSponsor;
        analysis=read(path.join(jobDir,"analysis.json"));showAnalysis();$("epSpeaker").value=old.config.hostSpeaker || "";analyzedSettings=analysisSettings(current());
        if(fs.existsSync(path.join(jobDir,"plan.json"))){plan=read(path.join(jobDir,"plan.json"));showPlan();}
        setBusy(false);status("Saved review restored. Media and timeline will be checked before building.","ok");
      })));
    }
  } catch(e){status("Full Episode settings: "+e.message,"err");}
}());
