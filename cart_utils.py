from flask import Flask, request, jsonify, session

app = Flask(__name__)
app.secret_key = 'default_secret_key'

# Sample data
products = {
    'sku1': {'tier': 'tier1', 'price': 10.0},
    'sku2': {'tier': 'tier2', 'price': 20.0},
}

def add_to_cart(sku, tier, price):
    from flask import session
    cart = session.get('cart', {})
    key = f"{sku}:{tier}"
    if key in cart:
        cart[key]['units'] += 1
    else:
        cart[key] = {
            'sku': sku,
            'tier': tier,
            'price': float(price),
            'units': 1
        }
    session['cart'] = cart
    total_units = sum(item['units'] for item in cart.values())
    session['cart_count'] = total_units
    return {"status": "success", "new_total": total_units}

@app.route('/add_to_cart', methods=['POST'])
def add_to_cart_route():
    data = request.get_json()
    sku = data['sku']
    tier = data['tier']
    price = data['price']
    result = add_to_cart(sku, tier, price)
    return jsonify(result)

@app.route('/cart', methods=['GET'])
def cart_route():
    cart = session.get('cart', {})
    return jsonify(cart)

@app.route('/cart_count', methods=['GET'])
def cart_count_route():
    cart_count = session.get('cart_count', 0)
    return jsonify({'cart_count': cart_count})

if __name__ == '__main__':
    app.run(debug=True)