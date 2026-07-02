/* deck-module */
function deckSetSkin(root, skin) {
  root.classList.toggle('cassette-mode', skin === 'cassette');
}
function deckSetPlaying(root, on) {
  root.classList.toggle('playing', on);
  var arm = document.getElementById('arm');
  if (arm) arm.classList.toggle('on', on);
}
function deckSmokeAt(root) {
  function centerOf(el) { var r = el.getBoundingClientRect(); return {x: r.left + r.width/2, y: r.top + r.height/2}; }
  if (root.classList.contains('cassette-mode'))
    return [].slice.call(document.querySelectorAll('#cassette .reel')).map(centerOf);
  var t = document.getElementById('armtip');
  return t ? [centerOf(t)] : [];
}
function deckFx(root, name, on) {
  root.classList.toggle('off-' + name, !on);
}
