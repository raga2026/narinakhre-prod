// retail_cart_state.js: Persist and sync retail cart quantities and unit counter UI across all retail pages

function getRetailCart() {
    try {
        return JSON.parse(localStorage.getItem('retail_cart') || '{}');
    } catch {
        return {};
    }
}
function setRetailCart(cart) {
    localStorage.setItem('retail_cart', JSON.stringify(cart));
}
function updateRetailCart(sku, qty) {
    const cart = getRetailCart();
    if (qty > 0) {
        cart[sku] = qty;
    } else {
        delete cart[sku];
    }
    setRetailCart(cart);
}
function clearRetailCart() {
    localStorage.removeItem('retail_cart');
    // Broadcast clear event for all tabs/pages
    try {
        localStorage.setItem('retail_cart_cleared', Date.now().toString());
    } catch (e) {}
}

function syncRetailUnitCounters() {
    const cart = getRetailCart();
    document.querySelectorAll('.cart-container').forEach(container => {
        const addBtn = container.querySelector('.add-to-cart-btn');
        const unitCounter = container.querySelector('.unit-counter');
        const qtySel = container.querySelector('.qty-select');
        const qtyDisplay = container.querySelector('.qty-display');
        const sku = addBtn ? addBtn.getAttribute('data-sku') : null;
        if (!sku) return;
        const qty = cart[sku] || 0;
        if (qty > 0) {
            if (addBtn) addBtn.style.display = 'none';
            if (unitCounter) unitCounter.classList.remove('hidden');
            if (qtySel) qtySel.value = qty;
            if (qtyDisplay) qtyDisplay.textContent = qty;
        } else {
            if (addBtn) addBtn.style.display = '';
            if (unitCounter) unitCounter.classList.add('hidden');
            if (qtySel) qtySel.value = 1;
            if (qtyDisplay) qtyDisplay.textContent = 1;
        }
    });
}

document.addEventListener('DOMContentLoaded', function() {
    syncRetailUnitCounters();
    // Listen for cart clear event from other tabs/pages
    window.addEventListener('storage', function(e) {
        if (e.key === 'retail_cart_cleared') {
            syncRetailUnitCounters();
        }
    });
    // Hook up all retail cart actions to update localStorage
    document.querySelectorAll('.cart-container').forEach(container => {
        const addBtn = container.querySelector('.add-to-cart-btn');
        const unitCounter = container.querySelector('.unit-counter');
        const qtySel = container.querySelector('.qty-select');
        const minusBtn = container.querySelector('.decrement-btn');
        const plusBtn = container.querySelector('.increment-btn');
        const qtyDisplay = container.querySelector('.qty-display');
        const sku = addBtn ? addBtn.getAttribute('data-sku') : null;
        if (!sku) return;
        if (addBtn) {
            addBtn.addEventListener('click', function() {
                const qty = parseInt(qtySel ? qtySel.value : 1);
                updateRetailCart(sku, qty);
                setTimeout(syncRetailUnitCounters, 100);
            });
        }
        if (minusBtn) {
            minusBtn.addEventListener('click', function() {
                let qty = parseInt(qtyDisplay ? qtyDisplay.textContent : 1);
                qty = Math.max(0, qty - 1);
                updateRetailCart(sku, qty);
                setTimeout(syncRetailUnitCounters, 100);
            });
        }
        if (plusBtn) {
            plusBtn.addEventListener('click', function() {
                let qty = parseInt(qtyDisplay ? qtyDisplay.textContent : 1);
                qty = Math.min(5, qty + 1);
                updateRetailCart(sku, qty);
                setTimeout(syncRetailUnitCounters, 100);
            });
        }
        if (qtySel) {
            qtySel.addEventListener('change', function() {
                const qty = parseInt(qtySel.value);
                updateRetailCart(sku, qty);
                setTimeout(syncRetailUnitCounters, 100);
            });
        }
    });
});

// To clear cart after checkout/payment, call clearRetailCart() after successful payment.
