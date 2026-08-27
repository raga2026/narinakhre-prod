// cart_actions.js: Handles Add to Quote logic, cart counter sync, and Continue button updates for Home and Product pages
function updateTierPrice(sku) {
    const select = document.getElementById('tier-select-' + sku);
    if (!select) return;
    const price = select.options[select.selectedIndex].getAttribute('data-price');
    document.getElementById('price-display-' + sku).innerText = '₹' + price;
}
function updateCartUI(newTotal) {
    const cartCount = document.getElementById('cart-count');
    if (cartCount) cartCount.innerText = newTotal;
    document.querySelectorAll('.footer-cart-count').forEach(el => el.innerText = newTotal);
    document.querySelectorAll('.continue-btn').forEach(btn => {
        btn.innerText = `Continue with ${newTotal} Units in Quote`;
    });
    const ariaLive = document.getElementById('aria-live-quote');
    if (ariaLive) ariaLive.innerText = `Quote updated. Total units: ${newTotal}.`;
}
function addToQuote(sku, name, tierSource) {
    let tierQty, price;
    if (tierSource === 'dropdown') {
        const select = document.getElementById('tier-select-' + sku);
        tierQty = select.value;
        price = select.options[select.selectedIndex].getAttribute('data-price');
    } else {
        // Home page card: find closest tier select
        const card = document.getElementById('product-card-' + sku);
        const select = card ? card.querySelector('.tier-select') : null;
        tierQty = select ? select.value : 1;
        price = select ? select.options[select.selectedIndex].getAttribute('data-price') : 0;
    }
    fetch('/add_to_cart', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ sku, tier: tierQty, price })
    })
    .then(res => res.json())
    .then(data => {
        updateCartUI(data.new_total);
        // Product page: replace button with unit selector
        const quoteAction = document.getElementById('quote-action-' + sku);
        if (quoteAction) {
            quoteAction.innerHTML = `
                <div class='flex items-center gap-2' aria-label='Adjust units for this item'>
                    <button onclick='updateUnits("${sku}", "${tierQty}", 0)' class='rose-btn px-3 py-1 rounded-lg'>-</button>
                    <span id='units-${sku}-${tierQty}'>1</span>
                    <button onclick='updateUnits("${sku}", "${tierQty}", 2)' class='rose-btn px-3 py-1 rounded-lg'>+</button>
                </div>
            `;
        }
    });
}
window.addEventListener('DOMContentLoaded', function() {
    // Attach Add to Quote for Home page cards
    document.querySelectorAll('.add-to-quote-btn').forEach(btn => {
        btn.onclick = function() {
            const sku = btn.getAttribute('data-sku');
            addToQuote(sku, btn.getAttribute('data-name'), 'card');
        };
    });
    // Attach Add to Quote for Product page
    document.querySelectorAll('.add-to-quote-btn-product').forEach(btn => {
        btn.onclick = function() {
            const sku = btn.getAttribute('data-sku');
            addToQuote(sku, btn.getAttribute('data-name'), 'dropdown');
        };
    });
});
