def render_quote_email(name, address, display_cart, grand_total):
    cart_table = """
    <table style='width:100%;border-collapse:collapse;margin-bottom:24px;'>
        <thead style='background:#be185d;color:#fff;'>
            <tr>
                <th style='padding:8px;border:1px solid #be185d;'>Name</th>
                <th style='padding:8px;border:1px solid #be185d;'>Quantity</th>
                <th style='padding:8px;border:1px solid #be185d;'>Pieces</th>
                <th style='padding:8px;border:1px solid #be185d;'>Price</th>
            </tr>
        </thead>
        <tbody>
    """
    for item in display_cart:
        cart_table += f"<tr>"
        cart_table += f"<td style='padding:8px;border:1px solid #be185d;'>{item['name']}</td>"
        cart_table += f"<td style='padding:8px;border:1px solid #be185d;'>{item['units']}</td>"
        cart_table += f"<td style='padding:8px;border:1px solid #be185d;'>{item['qty'] if 'qty' in item else item.get('tier', '')}</td>"
        cart_table += f"<td style='padding:8px;border:1px solid #be185d;'>₹{item['price']}</td>"
        cart_table += "</tr>"
    cart_table += """
        </tbody>
    </table>
    """
    # Ensure grand_total is numeric for comparison
    try:
        grand_total_num = float(grand_total)
    except (ValueError, TypeError):
        grand_total_num = 0
    body = f"""
    <div style='padding:32px 0;text-align:center;'>
        <img src='https://narinakhre.com/static/assets/logo.jpg' alt='Nari Nakhre Logo' style='height:60px;margin-bottom:16px;'>
    </div>
    <h2 style='color:#be185d;'>Dear {name},</h2>
    <p style='font-size:1.1em;'>Thank you for choosing <strong>Nari Nakhre</strong>! Please find your quote below:</p>
    {cart_table if display_cart else '<div style="color:#be185d;font-weight:bold;">No items found in your quote cart.</div>'}
    <div style='margin-top:24px;font-size:1.1em;'>
        <strong>Total Tentative Price:</strong> ₹{grand_total if grand_total_num > 0 else 'N/A'}<br>
        <strong>Address:</strong> {address}
    </div>
    <div style='margin-top:40px;padding:24px 0;background:#f8f9fa;border-radius:16px;text-align:center;'>
        <div style='font-size:1.2em;color:#be185d;font-weight:bold;margin-bottom:8px;'>Mohini Cosmetics</div>
        <div style='color:#222;margin-bottom:4px;'>GSTIN: 23JFGPS7650J1ZV</div>
        <div style='color:#222;margin-bottom:4px;'>136, Dharhai Rd, Kotwali, Jabalpur, Madhya Pradesh 482001</div>
        <div style='color:#222;margin-bottom:4px;'>Mob: +91 71572310, email: mohinicosmetics.india@gmail.com</div>
        <div style='color:#222;margin-bottom:8px;'>website: <a href='https://narinakhre.com' style='color:#be185d;text-decoration:none;'>narinakhre.com</a> / <a href='https://wholesale.narinakhre.com' style='color:#be185d;text-decoration:none;'>wholesale.narinakhre.com</a></div>
        <div style='margin-top:24px;font-size:1.1em;font-weight:bold;color:#be185d;'>Visit Us At:</div>
        <a href='https://fb.com/narinakhre' target='_blank'><img src='https://narinakhre.com/static/facebook-logo.png' alt='Facebook' style='height:24px;vertical-align:middle;margin-right:8px;'></a>
        <a href='https://instagram.com/narinakhre' target='_blank'><img src='https://narinakhre.com/static/instagram-logo.jpg' alt='Instagram' style='height:24px;vertical-align:middle;margin-right:8px;'></a>
        <a href='https://narinakhre.com' target='_blank'><img src='https://narinakhre.com/static/assets/logo.jpg' alt='Nari Nakhre Logo' style='height:24px;vertical-align:middle;margin-right:8px;'></a>
    </div>
    """
    return body
