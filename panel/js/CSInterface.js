/* Minimal CSInterface: only what this panel uses (evalScript, extension path). */
function CSInterface() {}

CSInterface.prototype.evalScript = function (script, callback) {
  window.__adobe_cep__.evalScript(script, callback || function () {});
};

CSInterface.prototype.getExtensionPath = function () {
  var p = decodeURI(window.__adobe_cep__.getSystemPath("extension"));
  return p.replace(/^file:\/{2,3}/, "");
};
