/**
  Minimal post processor for the De-Fusion live checks (docs/specs/cam design 5.2).
  Writes a marker line, one G0/G1 line per move and a closing percent sign.
*/

description = "De-Fusion test post";
vendor = "De-Fusion";
vendorUrl = "";
legal = "Written for the De-Fusion MCP project.";
certificationLevel = 2;
minimumRevision = 45702;

longDescription = "Minimal milling post used to verify cam.post_process.";

extension = "nc";
programNameIsInteger = false;
setCodePage("ascii");

capabilities = CAPABILITY_MILLING;
tolerance = spatial(0.002, MM);

minimumChordLength = spatial(0.25, MM);
minimumCircularRadius = spatial(0.01, MM);
maximumCircularRadius = spatial(1000, MM);
minimumCircularSweep = toRad(0.01);
maximumCircularSweep = toRad(180);
allowHelicalMoves = false;
allowedCircularPlanes = undefined; // linear moves only

var xyzFormat = createFormat({decimals:3});
var feedFormat = createFormat({decimals:1});

function onOpen() {
  writeln("(DEFUSION)");
}

function onSection() {
  writeln("(section " + (currentSection.getId() + 1) + ")");
}

function onRapid(x, y, z) {
  writeln("G0 X" + xyzFormat.format(x) + " Y" + xyzFormat.format(y) + " Z" + xyzFormat.format(z));
}

function onLinear(x, y, z, feed) {
  writeln("G1 X" + xyzFormat.format(x) + " Y" + xyzFormat.format(y) + " Z" + xyzFormat.format(z) +
    " F" + feedFormat.format(feed));
}

function onSectionEnd() {
  writeln("");
}

function onClose() {
  writeln("%");
}
