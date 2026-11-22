-- PostgreSQL demo schema and seed data for Customers, Orders, Products, and order_items.
-- This file runs automatically only when a PostgreSQL Docker volume is initialized.

CREATE TABLE IF NOT EXISTS customers (
    customer_id VARCHAR(50) PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    status VARCHAR(50) NOT NULL
);

CREATE TABLE IF NOT EXISTS orders (
    order_id VARCHAR(50) PRIMARY KEY,
    customer_id VARCHAR(50) NOT NULL REFERENCES customers(customer_id),
    order_date DATE NOT NULL,
    status VARCHAR(50) NOT NULL
);

CREATE TABLE IF NOT EXISTS products (
    product_id VARCHAR(50) PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    category VARCHAR(100) NOT NULL,
    price NUMERIC(12,2) NOT NULL
);

CREATE TABLE IF NOT EXISTS order_items (
    order_id VARCHAR(50) NOT NULL REFERENCES orders(order_id),
    product_id VARCHAR(50) NOT NULL REFERENCES products(product_id),
    PRIMARY KEY (order_id, product_id)
);

INSERT INTO customers (customer_id, name, status) VALUES
    ('C001', 'Avery Morgan', 'active'),
    ('C002', 'Jordan Lee', 'active')
ON CONFLICT (customer_id) DO NOTHING;

INSERT INTO orders (order_id, customer_id, order_date, status) VALUES
    ('O100', 'C001', '2026-11-20', 'shipped'),
    ('O101', 'C002', '2026-11-21', 'processing')
ON CONFLICT (order_id) DO NOTHING;

INSERT INTO products (product_id, name, category, price) VALUES
    ('P100', 'Notebook', 'Office', 12.50),
    ('P200', 'Keyboard', 'Technology', 48.00)
ON CONFLICT (product_id) DO NOTHING;

INSERT INTO order_items (order_id, product_id) VALUES
    ('O100', 'P100'),
    ('O100', 'P200'),
    ('O101', 'P100')
ON CONFLICT DO NOTHING;
