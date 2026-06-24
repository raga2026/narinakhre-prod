// retail_unit_counter.js: Handles unit counter logic for retail pages

document.addEventListener('DOMContentLoaded', function() {
    // Attach event listeners to all retail cart containers
    document.querySelectorAll('.cart-container').forEach(function(container) {
        const addBtn = container.querySelector('.add-to-cart-btn');
        const buyBtn = container.querySelector('.buy-now-btn');
        const unitCounter = container.querySelector('.unit-counter');
        const qtySel = container.querySelector('.qty-select');
        const minusBtn = container.querySelector('.decrement-btn');
        const plusBtn = container.querySelector('.increment-btn');
        const qtyDisplay = container.querySelector('.qty-display');

        // Show unit counter after Add to Cart
        if (addBtn) {
            addBtn.addEventListener('click', function(e) {
                setTimeout(function() {
                    addBtn.style.display = 'none';
                    if (unitCounter) unitCounter.classList.remove('hidden');
                    if (qtyDisplay && qtySel) qtyDisplay.textContent = qtySel.value;
                }, 200); // Wait for cart update
            });
        }

        // Increment/Decrement logic
        if (minusBtn && plusBtn && qtyDisplay && qtySel) {
            minusBtn.addEventListener('click', function() {
                let val = parseInt(qtyDisplay.textContent);
                if (val > 1) {
                    val--;
                    qtyDisplay.textContent = val;
                    qtySel.value = val;
                    // Trigger change event for cart update
                    qtySel.dispatchEvent(new Event('change'));
                } else {
                    // Remove from cart, reset UI
                    if (unitCounter) unitCounter.classList.add('hidden');
                    if (addBtn) addBtn.style.display = '';
                    qtySel.value = 1;
                    qtyDisplay.textContent = 1;
                    qtySel.dispatchEvent(new Event('change'));
                }
            });
            plusBtn.addEventListener('click', function() {
                let val = parseInt(qtyDisplay.textContent);
                if (val < 5) {
                    val++;
                    qtyDisplay.textContent = val;
                    qtySel.value = val;
                    qtySel.dispatchEvent(new Event('change'));
                }
            });
        }

        // Sync dropdown and counter
        if (qtySel && qtyDisplay && unitCounter) {
            qtySel.addEventListener('change', function() {
                qtyDisplay.textContent = qtySel.value;
            });
        }
    });
});
