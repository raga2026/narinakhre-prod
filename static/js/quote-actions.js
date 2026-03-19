// quote-actions.js: Shared Add to Quote logic for Home and Product pages
function updateCartCount(newTotal) {
    document.querySelectorAll('#cart-count').forEach(el => el.innerText = newTotal);
    document.querySelectorAll('#footer-cart-count').forEach(el => el.innerText = newTotal);
}
function revertToAddToQuote(parent, sku, name, tier, price) {
    parent.innerHTML = `<button class='rose-btn add-to-quote-btn' data-sku='${sku}' data-name='${name}' data-price='${price}'>Add to Quote</button>`;
    parent.querySelector('.add-to-quote-btn').onclick = function() {
        addToQuote(sku, name, tier, price, parent);
    };
}
function replaceWithUnitSelector(parent, sku, name, tier, price, units) {
    parent.innerHTML = `
        <div class='flex items-center gap-2' aria-label='Adjust units for this item'>
            <button class='rose-btn px-3 py-1 rounded-lg minus-btn'>-</button>
            <span id='units-${sku}-${tier}'>${units}</span>
            <button class='rose-btn px-3 py-1 rounded-lg plus-btn'>+</button>
        </div>
    `;
    parent.querySelector('.minus-btn').onclick = function() {
        if (units > 1) {
            updateUnits(sku, tier, units - 1, parent, name, price);
        } else {
            revertToAddToQuote(parent, sku, name, tier, price);
        }
    };
    parent.querySelector('.plus-btn').onclick = function() {
        updateUnits(sku, tier, units + 1, parent, name, price);
    };
}
function updateUnits(sku, tier, units, parent, name, price) {
    fetch('/update-cart', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            product_id: sku,
            qty: units,
            tier: tier,
            price: price,
            size: '' // Add size if available from UI
        })
    })
    .then(res => res.json())
    .then(data => {
        if (units > 0) {
            replaceWithUnitSelector(parent, sku, name, tier, price, units);
        } else {
            revertToAddToQuote(parent, sku, name, tier, price);
        }
        updateCartCount(data.new_total_units);
    });
}
function addToQuote(sku, name, tier, price, parent) {
    fetch('/update-cart', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            product_id: sku,
            qty: 1,
            tier: tier,
            price: price,
            size: '' // Add size if available from UI
        })
    })
    .then(res => res.json())
    .then(data => {
        replaceWithUnitSelector(parent, sku, name, tier, price, 1);
        updateCartCount(data.new_total_units);
    });
}
document.addEventListener('DOMContentLoaded', function() {
    document.querySelectorAll('.add-to-quote-btn').forEach(function(btn) {
        btn.onclick = function() {
            const sku = btn.getAttribute('data-sku');
            const name = btn.getAttribute('data-name');
            const price = btn.getAttribute('data-price');
            const tierSelect = btn.closest('.product-card') ? btn.closest('.product-card').querySelector('.tier-select') : document.getElementById('tier-select-' + sku);
            const tier = tierSelect ? tierSelect.value : 1;
            const parent = btn.parentElement;
            addToQuote(sku, name, tier, price, parent);
        };
    });
});
