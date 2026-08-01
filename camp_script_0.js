
        let lastProductIds = [];

        function showMsg(text, isError) {
            const el = document.getElementById('campaign-msg');
            el.style.display = 'block';
            el.textContent = text;
            el.style.color = isError ? '#dc2626' : '#15803d';
        }

        function generatePreview() {
            const name = document.getElementById('cf-name').value.trim();
            const discount = document.getElementById('cf-discount').value;
            const maxAmount = document.getElementById('cf-max-amount').value;

            if (!name) { showMsg('Please enter a campaign name.', true); return; }
            if (!discount || discount <= 0 || discount > 100) { showMsg('Please enter a discount percent between 1 and 100.', true); return; }
            if (maxAmount === '') { showMsg('Please enter a max discount amount.', true); return; }

            const btn = document.getElementById('btn-generate');
            btn.disabled = true;
            btn.textContent = 'Generating…';

            fetch('"x"', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({name: name, discount_percent: discount, max_discount_amount: maxAmount})
            })
            .then(r => r.json())
            .then(d => {
                btn.disabled = false;
                btn.textContent = '✨ Generate Email';
                if (d.status !== 'success') { showMsg(d.message || 'Could not generate the email.', true); return; }
                document.getElementById('campaign-msg').style.display = 'none';
                lastProductIds = d.product_ids;
                document.getElementById('preview-wrap').style.display = 'block';
                const frame = document.getElementById('preview-frame');
                frame.srcdoc = d.html;
            })
            .catch(() => { btn.disabled = false; btn.textContent = '✨ Generate Email'; showMsg('Could not generate the email. Please try again.', true); });
        }

        function saveCampaign() {
            if (!lastProductIds.length) { showMsg('Please generate the email first.', true); return; }
            const form = document.createElement('form');
            form.method = 'POST';
            form.action = '"x"';
            const fields = {
                name: document.getElementById('cf-name').value.trim(),
                discount_percent: document.getElementById('cf-discount').value,
                max_discount_amount: document.getElementById('cf-max-amount').value,
                product_ids: JSON.stringify(lastProductIds)
            };
            Object.keys(fields).forEach(function(key) {
                const input = document.createElement('input');
                input.type = 'hidden'; input.name = key; input.value = fields[key];
                form.appendChild(input);
            });
            document.body.appendChild(form);
            form.submit();
        }
    