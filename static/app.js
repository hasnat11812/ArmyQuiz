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

  // Theme toggle: apply saved theme and wire up toggle button
  (function(){
    const key = 'armyquiz_theme';
    const btn = document.getElementById('theme-toggle-btn');
    // announcer element (create if missing)
    let announcer = document.querySelector('.a11y-announcer');
    if(!announcer){
      announcer = document.createElement('div');
      announcer.className = 'a11y-announcer';
      announcer.setAttribute('aria-live','polite');
      document.body.appendChild(announcer);
    }
    function applyTheme(theme){
      if(theme === 'light') document.documentElement.classList.add('theme-light');
      else document.documentElement.classList.remove('theme-light');
      if(btn) btn.setAttribute('aria-pressed', theme === 'light' ? 'true' : 'false');
      if(announcer) announcer.textContent = theme === 'light' ? 'Switched to day mode' : 'Switched to night mode';
    }
    // init
    const saved = localStorage.getItem(key);
    const prefersLight = window.matchMedia && window.matchMedia('(prefers-color-scheme: light)').matches;
    const initial = saved || (prefersLight ? 'light' : 'dark');
    applyTheme(saved);
    // If no explicit saved preference, listen for system changes
    if(!saved && window.matchMedia){
      const mm = window.matchMedia('(prefers-color-scheme: light)');
      mm.addEventListener && mm.addEventListener('change', e => {
        const auto = e.matches ? 'light' : 'dark';
        applyTheme(auto);
      });
    }

    if(btn){
      btn.addEventListener('click', function(e){
        const now = document.documentElement.classList.contains('theme-light') ? 'dark' : 'light';
        localStorage.setItem(key, now);
        applyTheme(now);
      });
    }
  })();

  // Confirm close-room forms to prevent accidental closure
  document.querySelectorAll('.close-room-form').forEach(function(form){
    form.addEventListener('submit', function(e){
      const name = form.getAttribute('data-room-name') || 'this room';
      const ok = window.confirm(`Are you sure you want to close "${name}"? This will prevent students from joining.`);
      if(!ok) e.preventDefault();
    });
  });

  // Student topbar mobile toggle
  (function(){
    const topbar = document.querySelector('.student-topbar');
    if(!topbar) return;
    const toggle = topbar.querySelector('.topbar-toggle');
    toggle && toggle.addEventListener('click', function(){
      const expanded = topbar.classList.toggle('expanded');
      toggle.setAttribute('aria-expanded', expanded ? 'true' : 'false');
    });
  })();

  // Teacher navbar hamburger toggle (mobile)
  (function(){
    const hamb = document.querySelector('.teacher-hamburger');
    const menu = document.querySelector('.teacher-menu');
    if(!hamb || !menu) return;
    hamb.addEventListener('click', function(){
      const expanded = menu.classList.toggle('show');
      hamb.setAttribute('aria-expanded', expanded ? 'true' : 'false');
    });
  })();

  // Create Quiz: Load/clear sample JSON button handlers
  (function(){
    const loadBtn = document.getElementById('load-sample-quiz');
    const clearBtn = document.getElementById('clear-sample-quiz');
    const textarea = document.getElementById('questions-json');
    const samplePre = document.getElementById('sample-quiz');
    if(!textarea) return;
    loadBtn && loadBtn.addEventListener('click', function(){
      if(samplePre) textarea.value = samplePre.textContent.trim();
      textarea.focus();
    });
    clearBtn && clearBtn.addEventListener('click', function(){
      textarea.value = '';
      textarea.focus();
    });
  })();

  // Mobile drawer toggle (open/close with hamburger/topbar)
  (function(){
    const drawer = document.getElementById('mobile-drawer');
    const backdrop = document.getElementById('drawer-backdrop');
    const closeBtn = document.getElementById('drawer-close');
    // openers: teacher hamburger and student topbar toggle
    const openers = Array.from(document.querySelectorAll('.teacher-hamburger, .topbar-toggle, .hamburger'));
    function openDrawer(){
      drawer && drawer.classList.add('show');
      backdrop && backdrop.classList.add('show');
      document.body.style.overflow = 'hidden';
      drawer && drawer.setAttribute('aria-hidden','false');
    }
    function closeDrawer(){
      drawer && drawer.classList.remove('show');
      backdrop && backdrop.classList.remove('show');
      document.body.style.overflow = '';
      drawer && drawer.setAttribute('aria-hidden','true');
    }
    openers.forEach(op => op && op.addEventListener('click', function(e){ e.preventDefault(); openDrawer(); }));
    backdrop && backdrop.addEventListener('click', closeDrawer);
    closeBtn && closeBtn.addEventListener('click', closeDrawer);
    document.addEventListener('keydown', function(e){ if(e.key === 'Escape') closeDrawer(); });
  })();
});
