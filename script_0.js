
/* ── Share ── */
async function shareProduct(name, url, imageUrl) {
    // Social platforms (WhatsApp, Facebook, Twitter) read OG tags from the URL
    // Web Share API on mobile also supports sharing files
    const shareData = {
        title: name + ' | Nari Nakhre',
        text: '✨ ' + name + ' — Premium Ethnic Jewellery by Nari Nakhre',
        url: url
    };
    // Try native share with image file (works on Android/iOS)
    if (imageUrl && navigator.canShare && window.File) {
        try {
            const r = await fetch(imageUrl, { mode: 'cors' });
            if (r.ok) {
                const blob = await r.blob();
                const file = new File([blob], name.replace(/\W+/g,'-').toLowerCase()+'.jpg', { type: blob.type });
                const sd = { ...shareData, files: [file] };
                if (navigator.canShare(sd)) { await navigator.share(sd); return; }
            }
        } catch(e) {}
    }
    // Native share without file
    if (navigator.share) {
        navigator.share(shareData).catch(()=>{});
        return;
    }
    // Desktop fallback: WhatsApp with product link (OG tags show image preview)
    const msg = encodeURIComponent(shareData.text + '\n' + url);
    window.open('https://wa.me/?text=' + msg, '_blank');
}

/* ── Offers carousel: tap to copy a code ── */
function copyOfferCode(code, btn) {
    navigator.clipboard.writeText(code).then(function () {
        var original = btn.textContent;
        btn.textContent = 'Copied!';
        setTimeout(function () { btn.textContent = original; }, 1500);
    });
}

document.addEventListener('DOMContentLoaded', function () {

    /* ── Offers carousel: auto-rotate slides ── */
    var offersSlides = document.querySelectorAll('#offers-carousel .offers-slide');
    if (offersSlides.length > 1) {
        var offersIdx = 0;
        setInterval(function () {
            offersSlides[offersIdx].classList.remove('active');
            offersIdx = (offersIdx + 1) % offersSlides.length;
            offersSlides[offersIdx].classList.add('active');
        }, 4000);
    }

    /* ── Hero background from Supabase ── */
    var heroSec = document.getElementById('hero-section');
    var heroImages = [];
    if (heroImages.length > 0) {
        var heroIdx = 0;
        function setHero() {
            heroSec.style.backgroundImage = "url('" + heroImages[heroIdx] + "')";
            heroIdx = (heroIdx + 1) % heroImages.length;
        }
        setHero();
        setInterval(setHero, 5000);
    }

    /* ── Product image carousel ── */
    document.querySelectorAll('.carousel-img[data-images]').forEach(function (img) {
        var allUrls = [];
        try { allUrls = JSON.parse(img.getAttribute('data-images') || '[]'); } catch(e) {}
        if (allUrls.length <= 1) return;

        // Pre-validate which images actually load before starting carousel
        var validUrls = [];
        var checked = 0;
        var defaultSrc = img.src; // first image already loaded

        function startCarousel() {
            if (validUrls.length <= 1) return; // only 1 real image, no carousel needed
            var ci = 0;
            setInterval(function () {
                ci = (ci + 1) % validUrls.length;
                img.src = validUrls[ci];
            }, 3000);
        }

        // Check each URL by attempting to load it in a hidden Image object
        allUrls.forEach(function(url) {
            var probe = new Image();
            probe.onload = function() {
                validUrls.push(url);
                checked++;
                if (checked === allUrls.length) startCarousel();
            };
            probe.onerror = function() {
                checked++;
                if (checked === allUrls.length) startCarousel();
            };
            probe.src = url;
        });
    });

    /* ── Trending shelf auto-scroll ──
       Advances by one card every few seconds, loops back to the start at the
       end. Pauses on hover/touch/keyboard-focus so it never fights a visitor
       who's actively browsing, pauses off-screen so it doesn't run forever
       in a background tab, and stays off entirely for prefers-reduced-motion. */
    (function initTrendingAutoScroll() {
        const grid = document.getElementById('trending-grid');
        if (!grid) return;
        if (window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;

        let timer = null;
        let paused = false;

        function step() {
            if (paused) return;
            const card = grid.querySelector('.prod-card');
            if (!card) return;
            const style = getComputedStyle(grid);
            const gap = parseFloat(style.columnGap || style.gap || '10') || 10;
            const advance = card.getBoundingClientRect().width + gap;
            const atEnd = grid.scrollLeft + grid.clientWidth >= grid.scrollWidth - 4;
            grid.scrollTo({ left: atEnd ? 0 : grid.scrollLeft + advance, behavior: 'smooth' });
        }
        function start() {
            if (timer) return;
            timer = setInterval(step, 3000);
        }
        function stop() {
            clearInterval(timer);
            timer = null;
        }

        grid.addEventListener('pointerenter', function () { paused = true; });
        grid.addEventListener('pointerleave', function () { paused = false; });
        grid.addEventListener('touchstart', function () { paused = true; }, { passive: true });
        grid.addEventListener('touchend', function () { setTimeout(function () { paused = false; }, 2000); });
        grid.addEventListener('focusin', function () { paused = true; });
        grid.addEventListener('focusout', function () { paused = false; });

        if ('IntersectionObserver' in window) {
            new IntersectionObserver(function (entries) {
                entries.forEach(function (entry) {
                    if (entry.isIntersecting) start(); else stop();
                });
            }, { threshold: 0.2 }).observe(grid);
        } else {
            start();
        }
    })();

    /* ── Cart logic ── */
    function getSize(container) {
        const card = container.closest('.product-card') || container;
        const s = card.querySelector('.size-select');
        return s ? s.value : 'Standard';
    }
    function updateCartCount(data) {
        const el = document.getElementById('cart-count');
        if (el && data && typeof data.cart_count !== 'undefined') el.textContent = data.cart_count;
    }

    document.querySelectorAll('.cart-container').forEach(function (container) {
        const addBtn = container.querySelector('.add-to-cart-btn');
        const buyBtn = container.querySelector('.buy-now-btn');
        const counter = container.querySelector('.unit-counter');
        const qtySel = container.querySelector('.qty-select');
        const minusBtn = container.querySelector('.decrement-btn');
        const plusBtn = container.querySelector('.increment-btn');
        const qtyDisplay = container.querySelector('.qty-display');

        function getQty() { return parseInt(qtySel ? qtySel.value : 1); }
        function postCart(sku, qty, price, size) {
            return fetch('/update-cart', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ product_id: sku, qty: qty, price: price, size: size })
            }).then(r => r.json());
        }

        if (addBtn) {
            addBtn.addEventListener('click', function (e) {
                e.preventDefault();
                const sku = addBtn.dataset.sku, price = addBtn.dataset.price;
                postCart(sku, getQty(), price, getSize(container)).then(data => {
                    addBtn.style.display = 'none';
                    if (counter) { counter.classList.remove('hidden'); if (qtyDisplay) qtyDisplay.textContent = getQty(); }
                    updateCartCount(data);
                });
            });
        }
        if (buyBtn) {
            buyBtn.addEventListener('click', function (e) {
                e.preventDefault();
                const sku = buyBtn.dataset.sku, price = buyBtn.dataset.price;
                postCart(sku, getQty(), price, getSize(container)).then(() => { window.location.href = '/retail/checkout'; });
            });
        }
        if (minusBtn) {
            minusBtn.addEventListener('click', function () {
                let val = parseInt(qtyDisplay.textContent);
                const sku = addBtn.dataset.sku, price = addBtn.dataset.price;
                if (val > 1) {
                    val--;
                    qtyDisplay.textContent = val;
                    postCart(sku, val, price, getSize(container)).then(updateCartCount);
                } else {
                    postCart(sku, 0, price, getSize(container)).then(updateCartCount);
                    counter.classList.add('hidden');
                    addBtn.style.display = '';
                    if (qtyDisplay) qtyDisplay.textContent = 1;
                }
            });
        }
        if (plusBtn) {
            plusBtn.addEventListener('click', function () {
                let val = parseInt(qtyDisplay.textContent);
                if (val < 5) {
                    val++;
                    qtyDisplay.textContent = val;
                    const sku = addBtn.dataset.sku, price = addBtn.dataset.price;
                    postCart(sku, val, price, getSize(container)).then(updateCartCount);
                }
            });
        }
    });
});
