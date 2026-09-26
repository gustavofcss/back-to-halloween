(function(){
  var reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  function initStatic(){
    var items = [].map.call(document.querySelectorAll('canvas[data-static]'), function(c){
      // resolução 1/3 do tamanho real -> "pixelão" (com image-rendering:pixelated no CSS)
      var w = Math.max(40, Math.round(c.offsetWidth/3)), h = Math.max(30, Math.round(c.offsetHeight/3));
      c.width = w; c.height = h;
      var ctx = c.getContext('2d'), frames = [];
      for (var f=0; f<6; f++){
        var d = ctx.createImageData(w,h);
        for (var y=0; y<h; y++){
          var band = Math.random()*22;                      // faixas horizontais levemente mais claras
          for (var x=0; x<w; x++){
            var k=(y*w+x)*4, v=Math.random()*190+band+25;
            d.data[k]=v;          // R cheio
            d.data[k+1]=v*0.6;    // G 60%  -> tom vermelho-rosado
            d.data[k+2]=v*0.72;   // B 72%
            d.data[k+3]=255;
          }
        }
        frames.push(d);
      }
      return {ctx:ctx, frames:frames, w:w, h:h, i:0, y:0};
    });
    if (!items.length) return;
    if (reduce){ items.forEach(function(it){ it.ctx.putImageData(it.frames[0],0,0); }); return; }
    setInterval(function(){
      items.forEach(function(it){
        it.i = (it.i+1) % it.frames.length;
        it.ctx.putImageData(it.frames[it.i],0,0);
        // linha clara descendo devagar (scan)
        it.y = (it.y + it.h/80) % (it.h*1.3);
        var g = it.ctx.createLinearGradient(0,it.y-10,0,it.y+10);
        g.addColorStop(0,'rgba(255,255,255,0)'); g.addColorStop(.5,'rgba(255,225,230,.4)'); g.addColorStop(1,'rgba(255,255,255,0)');
        it.ctx.fillStyle = g; it.ctx.fillRect(0,it.y-10,it.w,20);
      });
    }, 60);   // 60ms por quadro
  }

  function initEyes(){
    document.querySelectorAll('video.eyes').forEach(function(v){
      v.muted = true; v.defaultMuted = true; v.playbackRate = 0.45;   // olhos piscando devagar
      var p = v.play(); if (p) p.catch(function(){});
    });
  }

  function initCounter(){
    var el = document.getElementById('tv-counter'); if (!el) return;
    var s = 23*60+12, p = function(n){ return String(n).padStart(2,'0'); };
    setInterval(function(){ s++; el.textContent = p(Math.floor(s/3600)%24)+':'+p(Math.floor(s/60)%60)+':'+p(s%60); }, 1000);
  }

  function start(){ setTimeout(initStatic, 80); initEyes(); initCounter(); }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start); else start();
})();
