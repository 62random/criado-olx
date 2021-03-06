import json
import os
import sys

import jinja2
import lxml.html
import pandas as pd
import requests
from flask import Flask
from sqlalchemy import create_engine

app = Flask(__name__)

# CONFIGS
DIR = os.path.dirname(os.path.realpath(__file__))
HTML_PAGE_PATH = f'{DIR}/templates/index.html'

MESSENGER_PAGE_ACCESS_TOKEN = os.environ["PAGE_ACCESS_TOKEN"]
VERIFY_TOKEN = os.environ["VERIFY_TOKEN"]

DATABASE_URL = os.environ["DATABASE_URL"]
ADS_TABLE_NAME = 'ads'
WISHLIST_TABLE_NAME = 'wishlist'
ENGINE = create_engine(DATABASE_URL)
CONN = ENGINE.connect()

# FLASK ROUTES
if __name__ == '__main__':
    app.run(debug=True)


@app.route('/messenger', methods=['GET'])
def verify():
    if request.args.get("hub.mode") == "subscribe" and request.args.get("hub.challenge"):
        if not request.args.get("hub.verify_token") == VERIFY_TOKEN:
            return "Verification token mismatch", 403
        return request.args["hub.challenge"], 200
    return "Hello :)", 200


@app.route('/messenger', methods=['POST'])
def receive_message():
    data = request.get_json()
    try:
        if data["object"] == "page":
            for entry in data["entry"]:
                for messaging_event in entry["messaging"]:
                    if messaging_event.get("message"):
                        sender_id = messaging_event["sender"]["id"]
                        message_text = messaging_event["message"]["text"]
                        arr = message_text.split(' ')
                        command = arr[0].lower()
                        if command in functions:
                            functions[command](sender_id, ' '.join(arr[1:]))
    except Exception as e:
        log(e)


@app.route('/', methods=['GET'])
def webhook():
    return render_template('index.html')


@app.route('/update', methods=['GET'])
def update():
    criado()
    return 'OK', 200


# BOT LOGIC
def criado():
    results = {
        'item': [],
        'url': [],
        'title': [],
        'price': []
    }
    main_df = get_table(ADS_TABLE_NAME)
    wish_df = get_table(WISHLIST_TABLE_NAME)

    for u in wish_df['user'].unique():
        df = main_df[main_df.user == u]
        w_df = wish_df[wish_df.user == u]

        df = df[df['item'].isin(w_df['item'].unique())]

        for item in w_df['item'].unique():
            search_url = 'https://www.olx.pt/ads/q-' + item.replace(" ", "-")
            r = requests.get(search_url)
            root = lxml.html.fromstring(r.content)

            ads = root.xpath("//td[contains(@class,'offer')]")

            for ad in ads:
                url = ad.xpath(".//h3/a/@href")
                if len(url) == 0:
                    continue
                url = url[0]
                title = ad.xpath(".//h3/a/strong/text()")[0]
                price = ad.xpath(".//p[contains(@class,'price')]/strong/text()")[0].strip()
                price = float(price.replace("€", "").replace(",", "."))

                if df['url'].str.contains(url).sum() == 0:
                    save_ad(results, u, item, url, title, price)
                else:
                    if df[df['url'] == url]['price'].values[0] > price:
                        index = df[df['url'] == url].index
                        df.drop(index, axis=0, inplace=True)
                        save_ad(results, item, url, title, price)

        new_ads_flag = len(results['url']) > 0
        if new_ads_flag:
            df = pd.concat([df, pd.DataFrame(results)], axis=0).sort_values(['price'])
            df.to_sql(ADS_TABLE_NAME, CONN, if_exists='replace')
            message_results(u, results)
        print_index(df, new_ads_flag)
        print(f"Found {len(results['url'])} ads")


def add(id, item):
    df = get_table(WISHLIST_TABLE_NAME)
    df = df.append({'user': id, 'item': item}, ignore_index=True)
    set_table(WISHLIST_TABLE_NAME, df)
    send_message(id, f"Current items:\n{list(df[df['user' == id]]['item'].values)}")


def rem(id, item):
    df = get_table(WISHLIST_TABLE_NAME)
    df = df[df['item'] != item]
    set_table(WISHLIST_TABLE_NAME, df)
    send_message(id, f"Current items:\n{list(df[df['user' == id]]['item'].values)}")


def list_fun(id, _):
    df = get_table(WISHLIST_TABLE_NAME)
    send_message(id, f"Current items:\n{list(df[df['user' == id]]['item'].values)}")


def help_fun(id, _):
    send_message(id, """Supported commands:
                        'add name of item'
                        'rem name of item'
                        'list'
                        'help'""")


functions = {
    'add': add,
    'rem': rem,
    'help': help_fun,
    'list': list_fun
}


# UTILS
def get_table(table):
    if table == ADS_TABLE_NAME:
        df = pd.DataFrame(columns=['user', 'item', 'url', 'title', 'price'])
    elif table == WISHLIST_TABLE_NAME:
        df = pd.DataFrame(columns=['user', 'item'])

    try:
        df = pd.read_sql(f'select * from "{table}"', CONN)
    except Exception as e:
        log(e)

    return df


def set_table(table, df):
    df.to_sql(table, CONN, if_exists='replace')


def save_ad(r, user, item, url, title, price):
    r['user'].append(user)
    r['item'].append(item)
    r['url'].append(url)
    r['title'].append(title)
    r['price'].append(price)


def render_template(file_name, **context):
    return jinja2.Environment(loader=jinja2.FileSystemLoader(f"{DIR}/templates/")) \
        .get_template(file_name) \
        .render(context)


def print_index(df, flag):
    if not os.path.exists(HTML_PAGE_PATH) or flag:
        s = render_template('template.html', df=df)
        text_file = open(f'{DIR}/templates/index.html', "w")
        text_file.write(s)
        text_file.close()


def message_results(u, r):
    if len(r['url']) == 0:
        return
    message = ""
    for i in range(len(r['url'])):
        message += f"Item: {r['title'][i]}\nPreço: {r['price'][i]}\nUrl: {r['url'][i]}\n---\n"

    send_message(u, message)


def send_message(recipient_id, message_text):
    log(f"Sending message to {recipient_id}:\n {message_text}\n")

    params = {
        "access_token": MESSENGER_PAGE_ACCESS_TOKEN
    }
    headers = {
        "Content-Type": "application/json; charset=utf-8"
    }
    data = json.dumps({
        "recipient": {
            "id": recipient_id
        },
        "message": {
            "text": message_text
        }
    })
    r = requests.post("https://graph.facebook.com/v2.6/me/messages", params=params, headers=headers, data=data)
    if r.status_code != 200:
        log(r.status_code)
        log(r.text)


def log(message):
    print(str(message))
    sys.stdout.flush()
