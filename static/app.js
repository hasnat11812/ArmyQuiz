// Small app enhancements: auto-dismiss toasts and a tiny pagination helper

document.addEventListener('DOMContentLoaded', function(){
  // Auto-dismiss flash/toast messages
  const flashes = document.querySelectorAll('.flash, .toast');
  flashes.forEach(el => {
    // Add fade class for CSS entry
    el.classList.add('fade-in');
    // If it's a success/info, auto-dismiss after 4s
    const category = Array.from(el.classList).find(c => c.startsWith('flash-') || c === 'toast' || c === 'toast-success');
    const auto = !el.classList.contains('no-auto');
    if(auto){
      setTimeout(() => {
        el.classList.add('fade-out');
        setTimeout(() => el.remove(), 300);
      }, 4000);
    }
  });

  // Simple client-side pagination helper
  // Usage: container with data-page-size attribute, children with class .page-item
  document.querySelectorAll('[data-page-size]').forEach(function(container){
    const pageSize = parseInt(container.getAttribute('data-page-size') || 10, 10);
    const items = Array.from(container.querySelectorAll('.page-item'));
    if(items.length <= pageSize) return; // no pagination needed

    let currentPage = 1;
    const totalPages = Math.ceil(items.length / pageSize);

    function render(){
      items.forEach((it, idx) => {
        const page = Math.floor(idx / pageSize) + 1;
        it.style.display = (page === currentPage) ? '' : 'none';
      });
      // update pager UI
      const pager = container.querySelector('.pager');
      if(pager){
        pager.innerHTML = `Page ${currentPage} / ${totalPages}`;
      }
    }

    // create simple controls
    const controls = document.createElement('div');
    controls.className = 'controls';
    const prev = document.createElement('button'); prev.className='btn small'; prev.textContent='Prev';
    const next = document.createElement('button'); next.className='btn small'; next.textContent='Next';
    const pager = document.createElement('div'); pager.className='pager small muted'; pager.style.display='inline-flex'; pager.style.alignItems='center'; pager.style.gap='8px';

    prev.onclick = function(){ if(currentPage>1){ currentPage--; render(); }};
    next.onclick = function(){ if(currentPage<totalPages){ currentPage++; render(); }};

    controls.appendChild(prev); controls.appendChild(pager); controls.appendChild(next);
    container.appendChild(controls);

    render();
  });

  // Central logo loader animation removed
  // The previous implementation attempted to animate a `.loader .bar` inside
  // `.central-logo`. Since the loader/bar markup was removed per latest
  // design choices, do not query or attempt to animate those elements to
  // avoid console errors. If you reintroduce a loader, re-add animation here.
});
