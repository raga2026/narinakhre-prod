/**
 * order_tracking.js: Renders a live shipment tracking timeline into a
 * container element, given a waybill. Shared by the standalone "Track Your
 * Order" page and the per-order "Order Details" page so the same widget
 * isn't maintained in two places.
 */
function renderOrderTracking(waybill, containerId) {
    const el = document.getElementById(containerId);
    if (!el) return;

    function renderTracking(data) {
        if (!data.status) {
            el.innerHTML = `<div class="tk-empty">${data.msg || 'No tracking information available yet. Please check back soon.'}</div>`;
            return;
        }
        const statusColors = {
            'Delivered': '#16a34a',
            'In Transit': '#be185d',
            'Pending': '#d97706',
            'Dispatched': '#be185d',
            'RTO': '#dc2626',
            'Cancelled': '#dc2626'
        };
        const color = statusColors[data.current_status] || '#be185d';

        let html = `
            <div class="tk-status-badge" style="color:${color};">${data.current_status || 'Processing'}</div>
            ${data.status_location ? `<div class="tk-status-sub">📍 ${data.status_location}</div>` : ''}
            ${data.expected_delivery ? `<div class="tk-eta">Expected delivery: <strong>${data.expected_delivery}</strong></div>` : ''}
        `;

        if (data.scans && data.scans.length > 0) {
            html += '<div class="tk-timeline" style="margin-top:16px;">';
            const scans = [...data.scans].reverse();
            scans.forEach((s) => {
                html += `
                    <div class="tk-step">
                        <div class="tk-step-title">${s.status || 'Update'}</div>
                        <div class="tk-step-meta">${s.location || ''} ${s.datetime ? '· ' + s.datetime : ''}</div>
                    </div>
                `;
            });
            html += '</div>';
        }

        el.innerHTML = html;
    }

    fetch('/api/track/' + encodeURIComponent(waybill))
        .then(r => r.json())
        .then(renderTracking)
        .catch(() => {
            el.innerHTML = '<div class="tk-empty">Could not load tracking info. Please try again later.</div>';
        });
}
