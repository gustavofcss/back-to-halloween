(function(){
  var reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  function init(){
    var items = [].map.call(document.querySelectorAll('canvas[data-static]'), function(c){
      var w = Math.max(40, Math.round(c.offsetWidth/3)), h = Math.max(20, Math.round(c.offsetHeight/3));
      c.width = w; c.height = h;
      var ctx = c.getContext('2d'), frames = [];
      for (var f=0; f<6; f++){
        var d = ctx.createImageData(w,h);
        for (var y=0; y<h; y++){ var band = Math.random()*22;
          for (var x=0; x<w; x++){ var k=(y*w+x)*4, v=Math.random()*190+band+25;
            d.data[k]=v; d.data[k+1]=v*.6; d.data[k+2]=v*.72; d.data[k+3]=255; } }
        frames.push(d);
      }
      ctx.putImageData(frames[0],0,0);
      return {ctx:ctx, frames:frames, i:0};
    });
    if (reduce || !items.length) return;
    setInterval(function(){ items.forEach(function(it){ it.i=(it.i+1)%6; it.ctx.putImageData(it.frames[it.i],0,0); }); }, 70);
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init); else init();
  // vídeo dos olhos: garante autoplay no mudo e deixa mais lento
  document.addEventListener('DOMContentLoaded', function(){
    document.querySelectorAll('video.eyes').forEach(function(v){ v.muted=true; v.playbackRate=0.45; var p=v.play(); if(p) p.catch(function(){}); });
  });
})();
