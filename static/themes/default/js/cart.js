// cart.js: Global Add to Quote logic, cart counter sync, and Continue button updates
function updateCartUI(newTotal) {
    const cartCount = document.getElementById('cart-count');
    if (cartCount) cartCount.innerText = newTotal;
    document.querySelectorAll('.footer-cart-count').forEach(el => el.innerText = newTotal);
        // Removed all Continue button logic as per UI cleanup request
    const ariaLive = document.getElementById('aria-live-quote');
    if (ariaLive) ariaLive.innerText = `Quote updated. Total units: ${newTotal}.`;
}
function addToQuote(sku, name, tierQty, price) {
    fetch('/add_to_cart', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ sku, tier: tierQty, price })
    })
    .then(res => res.json())
    .then(data => {
        updateCartUI(data.new_total);
    });
}

function renderUnitSelector(sku, count) {
    const actionDiv = document.getElementById(`action-${sku}`) || document.getElementById(`quote-action-${sku}`);
    if (!actionDiv) return;
    actionDiv.innerHTML = `
        <div class="flex items-center justify-between bg-slate-800 border border-pink-500 rounded-xl p-1 glow-pink">
            <button onclick="updateUnits('${sku}', -1)" class="px-4 py-1 text-pink-400 font-bold text-2xl">-</button>
            <span id="unit-count-${sku}" class="text-white font-bold text-lg">${count}</span>
            <button onclick="updateUnits('${sku}', 1)" class="px-4 py-1 text-pink-400 font-bold text-2xl">+</button>
        </div>`;
}

function updateUnits(sku, delta) {
    let cart = JSON.parse(localStorage.getItem('cart') || '{}');
    let item = cart[sku] || { count: 1 };
    item.count += delta;
    if (item.count <= 0) {
        delete cart[sku];
        localStorage.setItem('cart', JSON.stringify(cart));
        location.reload();
        return;
    }
    cart[sku] = item;
    localStorage.setItem('cart', JSON.stringify(cart));
    document.getElementById(`unit-count-${sku}`).innerText = item.count;
    updateCartBadge();
}

function showSelectorIfInCart() {
    let cart = JSON.parse(localStorage.getItem('cart') || '{}');
    Object.keys(cart).forEach(sku => {
        renderUnitSelector(sku, cart[sku].count);
    });
}

window.addEventListener('DOMContentLoaded', function() {
    showSelectorIfInCart();
});
