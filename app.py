from portfolio import Portfolio
from portfolio_group import PortfolioGroup
from asset import Asset

from flask import Flask, render_template, session, url_for, request, redirect, Response, flash
import io
import pandas as pd
import hashlib
import re


app = Flask(__name__)
app.secret_key = 'rexql;cnreqQVEJR'

@app.errorhandler(404)
def page_not_found(e):
    return render_template("404.html"), 404

@app.errorhandler(500)
def internal_error(e):
    return render_template('500.html'), 500

@app.route('/')
def index():
    return render_template('portfolio.html')

if __name__ == '__main__':
    app.run(debug=True)