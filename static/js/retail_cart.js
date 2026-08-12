/**
 * retail_cart.js: Single, authoritative Add to Cart / quantity counter logic
 * for retail product cards (homepage Trending shelf + grouped sections,
 * category pages, product detail page).
 *
 * This replaces what used to be three near-identical inline <script> blocks
 * copy-pasted across those templates -- that duplication is what caused the
 * earlier "two counters" / "quantity doubles" bugs, so all of that logic now
 * lives here once instead.
 *
 * Cart lines are keyed server-side by `${sku}_${size}` (see /update-cart),
 * meaning two sizes of the same product are genuinely separate cart entries.
 * The counter widget on a product card is pinned to whichever size it was
 * last shown for via `counter.dataset.activeSize`, set on Add to Cart and
 * re-checked whenever the size dropdown changes -- so +/- clicks always
 * target the size the counter is actually displaying, never whatever the
 * dropdown happens to say at click time.
 */
(function () {
    function findSizeSelect(container) {
        const card = container.closest('.product-card') || container.parentElement || container;
        return card.querySelector('.size-select') || document.getElementById('size-main');
    }
    function currentSize(container) {
        const sel = findSizeSelect(container);
        return sel ? sel.value : '';
    }
    function currentQty(container) {
        const qtySel = container.querySelector('.qty-select');
        return qtySel ? parseInt(qtySel.value, 10) : 1;
    }
    function postCart(sku, qty, price, size) {
        return fetch('/update-cart', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ product_id: sku, qty: qty, price: price, size: size })
        }).then(function (r) { return r.json(); });
    }
    function fetchCart() {
        return fetch('/cart').then(function (r) { return r.ok ? r.json() : {}; }).catch(function () { return {}; });
    }
    function qtyInCart(cart, sku, size) {
        const item = cart[sku + '_' + (size || '')];
        return item ? (item.qty || 0) : 0;
    }
    function updateCartCount(data) {
        if (!data || typeof data.new_total === 'undefined') return;
        if (typeof syncCartCount === 'function') syncCartCount(data.new_total);
        const el = document.getElementById('cart-count');
        if (el) el.textContent = data.new_total;
    }

    // One shared fetch for the page-load restore pass -- every card's
    // initial state check reuses this instead of firing N identical
    // requests. Size-switch checks below always fetch fresh instead, since
    // those happen well after load and the cart may have changed since.
    let initialCartPromise = null;
    function initialCart() {
        if (!initialCartPromise) initialCartPromise = fetchCart();
        return initialCartPromise;
    }

    function showCounter(container, size, qty) {
        const addBtn = container.querySelector('.add-to-cart-btn');
        const counter = container.querySelector('.unit-counter');
        const qtyDisplay = container.querySelector('.qty-display');
        const sizeLabel = container.querySelector('.counter-size-label');
        if (addBtn) addBtn.classList.add('hidden');
        if (counter) {
            counter.classList.remove('hidden');
            counter.dataset.activeSize = size || '';
        }
        if (qtyDisplay) qtyDisplay.textContent = qty;
        if (sizeLabel) {
            if (size) {
                sizeLabel.textContent = 'Size: ' + size;
                sizeLabel.classList.remove('hidden');
            } else {
                sizeLabel.textContent = '';
                sizeLabel.classList.add('hidden');
            }
        }
    }

    function showAddButton(container) {
        const addBtn = container.querySelector('.add-to-cart-btn');
        const counter = container.querySelector('.unit-counter');
        const qtyDisplay = container.querySelector('.qty-display');
        const sizeLabel = container.querySelector('.counter-size-label');
        if (addBtn) addBtn.classList.remove('hidden');
        if (counter) {
            counter.classList.add('hidden');
            delete counter.dataset.activeSize;
        }
        if (qtyDisplay) qtyDisplay.textContent = 1;
        if (sizeLabel) {
            sizeLabel.textContent = '';
            sizeLabel.classList.add('hidden');
        }
    }

    document.addEventListener('DOMContentLoaded', function () {
        if (typeof getSiteMode === 'function' && getSiteMode() !== 'retail') return;

        document.querySelectorAll('.cart-container').forEach(function (container) {
            const addBtn = container.querySelector('.add-to-cart-btn');
            const buyBtn = container.querySelector('.buy-now-btn');
            const counter = container.querySelector('.unit-counter');
            const qtyDisplay = container.querySelector('.qty-display');
            const minusBtn = container.querySelector('.decrement-btn');
            const plusBtn = container.querySelector('.increment-btn');
            const sizeSelect = findSizeSelect(container);
            if (!addBtn || !counter || !qtyDisplay) return;

            const sku = addBtn.dataset.sku;
            const price = addBtn.dataset.price;

            // Restore counter/button state from the real cart on page load,
            // for whichever size is selected by default.
            initialCart().then(function (cart) {
                const size = currentSize(container);
                const qty = qtyInCart(cart, sku, size);
                if (qty > 0) showCounter(container, size, qty);
            });

            addBtn.addEventListener('click', function (e) {
                e.preventDefault();
                const size = currentSize(container);
                const qty = currentQty(container);
                postCart(sku, qty, price, size).then(function (data) {
                    showCounter(container, size, qty);
                    updateCartCount(data);
                });
            });

            if (buyBtn) {
                buyBtn.addEventListener('click', function (e) {
                    e.preventDefault();
                    const size = currentSize(container);
                    const qty = currentQty(container);
                    postCart(sku, qty, price, size).then(function () {
                        window.location.href = '/retail/checkout';
                    });
                });
            }

            if (minusBtn) {
                minusBtn.addEventListener('click', function () {
                    const size = counter.dataset.activeSize || '';
                    const qty = parseInt(qtyDisplay.textContent, 10) - 1;
                    if (qty <= 0) {
                        postCart(sku, 0, price, size).then(function (data) {
                            showAddButton(container);
                            updateCartCount(data);
                        });
                    } else {
                        postCart(sku, qty, price, size).then(function (data) {
                            qtyDisplay.textContent = qty;
                            updateCartCount(data);
                        });
                    }
                });
            }

            if (plusBtn) {
                plusBtn.addEventListener('click', function () {
                    const size = counter.dataset.activeSize || '';
                    const qty = parseInt(qtyDisplay.textContent, 10) + 1;
                    postCart(sku, qty, price, size).then(function (data) {
                        qtyDisplay.textContent = qty;
                        updateCartCount(data);
                    });
                });
            }

            if (sizeSelect) {
                sizeSelect.addEventListener('change', function () {
                    // Only relevant once a counter is already showing --
                    // otherwise the next Add to Cart click just reads
                    // whatever size is selected then, which already works.
                    if (counter.classList.contains('hidden')) return;
                    const newSize = sizeSelect.value;
                    fetchCart().then(function (cart) {
                        const qty = qtyInCart(cart, sku, newSize);
                        if (qty > 0) {
                            showCounter(container, newSize, qty);
                        } else {
                            showAddButton(container);
                        }
                    });
                });
            }
        });
    });
})();
