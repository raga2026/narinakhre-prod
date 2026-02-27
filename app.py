import os
import sqlite3
import shutil
import smtplib
import json
import pandas as pd
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, session
from werkzeug.utils import secure_filename
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from functools import wraps

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = 'static/assets/uploads'
app.config['DATABASE'] = 'narinakhre.db'
app.secret_key = 'supersecretkey'

# SMTP/EMAIL CONFIG
app.config['MAIL_SERVER'] = 'smtp.gmail.com'
app.config['MAIL_PORT'] = 587
app.config['MAIL_USE_TLS'] = True
app.config['MAIL_USERNAME'] = 'narinakhre@gmail.com'
app.config['MAIL_PASSWORD'] = 'YOUR_16_CHAR_APP_PASSWORD' 
ADMIN_EMAIL = app.config['MAIL_USERNAME']

def get_db():
    conn = sqlite3.connect(app.config['DATABASE'])
    conn.row_factory = sqlite3.Row
    return conn

def get_nav_data():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT name FROM categories')
    nav_categories = [row['name'] for row in cursor.fetchall()]
    conn.close()
    return nav_categories

@app.route('/')
def index():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT p.*, c.name as category_name FROM products p LEFT JOIN categories c ON p.category_id = c.id')
    products = cursor.fetchall()
    grouped_products = {}
    for product in products:
        cat = product['category_name'] or 'Uncategorized'
        if cat not in grouped_products:
            grouped_products[cat] = []
        
        # Build image list
        images = [f"assets/products/{product['sku']}_{i}.jpg" for i in range(1, 6) if os.path.exists(os.path.join('static', 'assets', 'products', f"{product['sku']}_{i}.jpg"))]
        if not images: images = ['assets/coming-soon.jpg']
        
        # Get price tiers
        cursor.execute('SELECT min_qty, price FROM price_tiers WHERE product_id=? ORDER BY min_qty', (product['id'],))
        tiers = [{'qty': r['min_qty'], 'price': r['price']} for r in cursor.fetchall()]

        grouped_products[cat].append({
            'sku': product['sku'], 'name': product['name'], 'images': images,
            'sizes': product['sizes'], 'price_tiers': tiers
        })
    conn.close()
    return render_template('index.html', grouped_products=grouped_products, nav_categories=get_nav_data())

@app.route('/product/<sku>')
def product_detail(sku):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT p.*, c.name as category_name FROM products p LEFT JOIN categories c ON p.category_id = c.id WHERE p.sku = ?', (sku,))
    product = cursor.fetchone()
    if not product:
        conn.close()
        return "Product Not Found", 404
    
    cursor.execute('SELECT min_qty, price FROM price_tiers WHERE product_id=? ORDER BY min_qty', (product['id'],))
    price_tiers = [{'qty': r['min_qty'], 'price': r['price']} for r in cursor.fetchall()]
    conn.close()
    return render_template('product_detail.html', product=dict(product), price_tiers=price_tiers, nav_categories=get_nav_data())

@app.route('/cart')
def cart():
    return render_template('cart.html', nav_categories=get_nav_data())

@app.route('/submit-quote', methods=['POST'])
def submit_quote():
    data = request.get_json()
    name = data.get('name')
    whatsapp = data.get('whatsapp', '').replace(' ', '').replace('-', '')
    address = data.get('address')
    email = data.get('email', '')
    cart_items = data.get('cart', [])
    password = data.get('password') # Optional for registration
    
    total_amount = sum(item.get('Fixed_Price', 0) * item.get('Fixed_Qty', 0) * item.get('Sets_Count', 0) for item in cart_items)
    
    conn = get_db()
    cursor = conn.cursor()
    
    # Save/Update User
    cursor.execute('INSERT OR REPLACE INTO users (name, whatsapp, email, password) VALUES (?, ?, ?, ?)', (name, whatsapp, email, password))
    
    # Save Quote
    cursor.execute('''ES (?, ?,INSERT INTO quotes (customer_name, whatsapp, email, address, cart_json, total_amount)
                      VALU ?, ?, ?, ?)''', (name, whatsapp, email, address, json.dumps(cart_items), total_amount))
    quote_id = cursor.lastrowid
    conn.commit()
    conn.close()

    return jsonify({'success': True, 'quote_id': quote_id})

if __name__ == '__main__':
    app.run(debug=True)