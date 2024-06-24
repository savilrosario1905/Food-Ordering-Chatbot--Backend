from flask import Flask, jsonify, request
from flask_mysqldb import MySQL
import bcrypt
from datetime import datetime
from flask_cors import CORS
import pandas as pd 
from surprise import Dataset, Reader, SVD
from surprise.model_selection import train_test_split
from collections import defaultdict
from decimal import Decimal

app = Flask(__name__)
CORS(app)

app.config['MYSQL_HOST'] = 'localhost'
app.config['MYSQL_USER'] = 'xavier'
app.config['MYSQL_PASSWORD'] = 'Xavier@1234'
app.config['MYSQL_DB'] = 'resturant_db'

mysql = MySQL(app)

def hash_password(password):
    password_bytes = password.encode('utf-8')
    return bcrypt.hashpw(password_bytes, bcrypt.gensalt())

def verify_password(plain_password, hashed_password):
    plain_password_bytes = plain_password.encode('utf-8')
    return bcrypt.checkpw(plain_password_bytes, hashed_password)

@app.route('/signup', methods=['POST'])
def signup():
    data = request.json
    username = data.get('username')
    password = data.get('password')
    email = data.get('email')
    created_at = datetime.now()
    updated_at = datetime.now()
    
    if not username or not password or not email:
        return jsonify({'error': 'Username, password, and email are required'}), 400
    
    hashed_password = hash_password(password)
    
    try:
        cursor = mysql.connection.cursor()
        cursor.execute("INSERT INTO user (username, password, email, created_at, updated_at) VALUES (%s, %s, %s, %s, %s)",
                       (username, hashed_password, email, created_at, updated_at))
        mysql.connection.commit()
        cursor.close()
        return jsonify({'message': 'User signed up successfully'}), 201
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/signin', methods=['POST'])
def signin():
    data = request.json
    username = data.get('username')
    password = data.get('password')
    
    if not username or not password:
        return jsonify({'error': 'Both username and password are required'}), 400
    
    try:
        cursor = mysql.connection.cursor()
        cursor.execute("SELECT * FROM user WHERE username = %s", (username,))
        user = cursor.fetchone()
        print(user)
        cursor.close()
        
        if user:
            hashed_password = user[2].encode('utf-8')  
            if verify_password(password, hashed_password):
               cursor = mysql.connection.cursor()
               query = "INSERT INTO sessions (username, sessionid) VALUES (%s, %s)"
               cursor.execute(query, (username, None))
               mysql.connection.commit()  
               cursor.close()      
               return jsonify({'message': 'User signed in successfully'}), 200
            else:
                return jsonify({'error': 'Invalid username or password'}), 401
        else:
            return jsonify({'error': 'User not found'}), 404
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/menu', methods=['GET'])
def get_menu():
    try:
        cursor = mysql.connection.cursor()
        cursor.execute("SELECT * FROM menu")
        menu_items = cursor.fetchall()
        cursor.close()
        
        menu_json = []
        for item in menu_items:
            menu_json.append({
                'id': item[0],
                'name': item[1],
                'description': item[2],
                'price': float(item[3]),
                'category': item[4],
                'image':item[5]
            })

        return jsonify(menu_json)

    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/webhook', methods=['POST'])
def webhook():
    req = request.get_json(silent=True, force=True)
    intent = req.get('queryResult').get('intent').get('displayName')
    session_id = req.get('session')
    parameters = req.get('queryResult').get('parameters')
    print(intent)
    if intent == 'order.add- context: ongoing-order':
        food_item = parameters.get('food_item')
        number = parameters.get('number')
        if food_item and number:
            cursor = mysql.connection.cursor()
            for item, qty in zip(food_item, number):
                cursor.execute("SELECT id FROM menu WHERE name = %s", (item,))
                item_id = cursor.fetchone()
                if item_id:
                    item_id = item_id[0]
                    cursor.execute("INSERT INTO sessionitemadd (session_id, item_id, item_count) VALUES (%s, %s, %s) ON DUPLICATE KEY UPDATE item_count = item_count + %s",
                                   (session_id, item_id, qty, qty))
                else:
                    return jsonify({'fulfillmentText': f"Item '{item}' not found in the menu."}), 404
            mysql.connection.commit()
            cursor.close()
            return jsonify({'fulfillmentText': 'Items added to your order. Would you like to add more?'})
    elif intent == 'new.order':
        cursor = mysql.connection.cursor()

        
        check_query = "SELECT COUNT(*) FROM sessions WHERE sessionid = %s"
        cursor.execute(check_query, (session_id,))
        result = cursor.fetchone()
        session_exists = result[0] > 0

        if not session_exists:
            
            update_query = "UPDATE sessions SET sessionid = %s WHERE sessionid IS NULL"
            cursor.execute(update_query, (session_id,))
            mysql.connection.commit()
        else:
            
            print("Session ID already exists in the table. Skipping SQL query.")

        
        if session_id:
            
            max_id_query = "SELECT MAX(id) FROM sessions"
            cursor.execute(max_id_query)
            max_id_result = cursor.fetchone()
            max_id = max_id_result[0]

            
            update_last_row_query = "UPDATE sessions SET sessionid = %s WHERE id = %s"
            cursor.execute(update_last_row_query, (session_id, max_id))
            mysql.connection.commit()
            print(f"Updated last row with new session ID: {session_id}")

    
            return generate_menu_for_webhook(session_id)
    elif intent == 'order.complete - contect: ongoing-order':
        cursor = mysql.connection.cursor()
        query = "SELECT username FROM sessions WHERE sessionid = %s"
        cursor.execute(query, (session_id,))
        result = cursor.fetchone()
        if result:
            default_username = result[0]
            print(f"Username associated with session ID {session_id}: {default_username}")
        else:
            print(f"No username found for session ID {session_id}")
        cursor.close()

        try:
            cursor = mysql.connection.cursor()
            cursor.execute("INSERT INTO order_status (sessionid, username, status) VALUES (%s, %s, %s)", (session_id, default_username, 'Pending'))
            order_id = cursor.lastrowid 
            cursor.execute("SELECT item_id, item_count FROM sessionitemadd WHERE session_id = %s", (session_id,)) 
            session_items = cursor.fetchall()
            total_order_price = 0
            order_summary = []
            for item_id, qty in session_items:
                cursor.execute("SELECT name, price FROM menu WHERE id = %s", (item_id,))
                item_data = cursor.fetchone()
                if item_data:
                    item_name, item_price = item_data
                    total_price = item_price * qty
                    total_order_price += total_price
                    order_summary.append(f"{item_name} x{qty}: ${total_price:.2f}")

                    cursor.execute(
                        "INSERT INTO orders (orderId, username, itemId, quantity, totalPrice) VALUES (%s, %s, %s, %s, %s)",
                        (order_id, default_username, item_id, qty, total_price)
                    )
                else:
                    mysql.connection.rollback()
                    return jsonify({'fulfillmentText': f"Item with ID {item_id} not found in the menu."}), 404

            cursor.execute("DELETE FROM sessionitemadd WHERE session_id = %s", (session_id,))
            mysql.connection.commit()
            cursor.close()

            order_details = '\n'.join(order_summary)
            fulfillment_text = f'Awesome. Your order has been placed! Order Id @{order_id}\n\nOrder Details:\n{order_details}\nTotal: ${total_order_price:.2f}'
            return jsonify({'fulfillmentText': fulfillment_text})

        except Exception as e:
                mysql.connection.rollback()
                return jsonify({'fulfillmentText': f"Error completing order: {str(e)}"}), 500   
    elif intent == 'tracking.order- context: ongoing-tracking':
        order_id = parameters.get('number')
        if not order_id:
            return jsonify({'fulfillmentText': 'Order ID is required for tracking.'})

        try:
            cursor = mysql.connection.cursor()
            cursor.execute("SELECT sessionid FROM order_status WHERE id = %s", (order_id,))
            result = cursor.fetchone()

            if not result:
                return jsonify({'fulfillmentText': 'Order ID not found.'})

            order_session_id = result[0]
            if order_session_id != session_id:
                return jsonify({'fulfillmentText': 'This order is out of session.'})

            cursor.execute("SELECT status FROM order_status WHERE id = %s", (order_id,))
            status = cursor.fetchone()[0]

            cursor.close()
            return jsonify({'fulfillmentText': f'The status of order ID {order_id} is {status}.'})
        
        except Exception as e:
            return jsonify({'fulfillmentText': f"Error tracking order: {str(e)}"})
    elif intent == 'order.remove - context: ongoing-order':
        food_item = parameters.get('food_item')
        print(food_item[0])
        if not food_item:
            return jsonify({'fulfillmentText': 'Food item is required for removal.'})
        try:
            cursor = mysql.connection.cursor()
            cursor.execute("SELECT id FROM menu WHERE name = %s", (food_item[0],))
            item_id = cursor.fetchone()
            print(item_id)
            if not item_id:
                cursor.close()
                return jsonify({'fulfillmentText': f"Item '{food_item}' not found in the menu."}), 404

            item_id = item_id[0]
            cursor.execute("SELECT item_count FROM sessionitemadd WHERE session_id = %s AND item_id = %s", (session_id, item_id))
            session_item = cursor.fetchone()
            print(session_item)
            if session_item:
                
                cursor.execute("DELETE FROM sessionitemadd WHERE session_id = %s AND item_id = %s LIMIT 1", (session_id, item_id))
                mysql.connection.commit()
                cursor.close()
                return jsonify({'fulfillmentText': f"Item '{food_item[0]}' removed from your current session additions."})

            
            cursor.execute("SELECT id FROM order_status WHERE sessionid = %s", (session_id,))
            result = cursor.fetchone()
            if not result:
                cursor.close()
                return jsonify({'fulfillmentText': f"Item '{food_item[0]}' is not in your current order."}), 404
            order_id = result[0]

            
            cursor.execute("DELETE FROM orders WHERE orderId = %s AND itemId = %s LIMIT 1", (order_id, item_id))

            
            cursor.execute("UPDATE order_status SET status = 'Modified' WHERE id = %s", (order_id,))

            
            cursor.execute("SELECT menu.name FROM orders INNER JOIN menu ON orders.itemId = menu.id WHERE orderId = %s", (order_id,))
            remaining_items = cursor.fetchall()

            mysql.connection.commit()
            cursor.close()

            
            if remaining_items:
                remaining_items_list = ', '.join(item[0] for item in remaining_items)
                fulfillment_text = f"Item '{food_item[0]}' removed from your order.\nRemaining items: {remaining_items_list}"
            else:
                fulfillment_text = f"Item '{food_item[0]}' removed from your order. Your order is now empty."

            return jsonify({'fulfillmentText': fulfillment_text})

        except Exception as e:
            mysql.connection.rollback()
            return jsonify({'fulfillmentText': f"Error removing item from order: {str(e)}"}), 500

    return jsonify({'fulfillmentText': 'I am not sure how to handle that intent.'})


def generate_menu_for_webhook(session_id):
    try:
        cursor = mysql.connection.cursor()
        query = "SELECT username FROM sessions WHERE sessionid = %s"
        cursor.execute(query, (session_id,))
        result = cursor.fetchone()
        if result:
            username = result[0]
            print(f"Username associated with session ID {session_id}: {username}")
        else:
            print(f"No username found for session ID {session_id}")
        cursor.close()
        if not username:
            return jsonify({'fulfillmentText': 'Username not provided.'}), 400

        cursor = mysql.connection.cursor()

        cursor.execute("SELECT username, itemId, quantity FROM orders WHERE username = %s", (username,))
        user_orders = cursor.fetchall()
        print("User Orders:", user_orders)

        cursor.execute("SELECT id, name, category, price FROM menu")
        menu_data = cursor.fetchall()
        print("Menu data:", menu_data)

        cursor.close()

        if not user_orders:
            return generate_normal_menu(menu_data)

        testset = [(username, str(item_id), int(quantity)) for username, item_id, quantity in user_orders]

        reader = Reader(line_format='user item rating', rating_scale=(1, 5))
        data = Dataset.load_from_df(pd.DataFrame(testset, columns=['username', 'itemId', 'quantity']), reader)
        print(data)
        trainset = data.build_full_trainset()
        # raw_ratings = trainset.build_testset()

        # # Convert raw ratings to DataFrame
        # df_ratings = pd.DataFrame(raw_ratings, columns=['username', 'itemId', 'quantity'])

        # print(df_ratings)
        algo = SVD()
        algo.fit(trainset)
        user_recommendations = get_top_n_recommendations(algo, trainset, username)
        recommended_menu_dict, remaining_menu_dict = process_recommendations(menu_data, user_recommendations)
        return construct_menu_response(username, recommended_menu_dict, remaining_menu_dict)

    except Exception as e:
        print("Exception occurred:", e)
        return jsonify({'fulfillmentText': f"Error generating menu: {str(e)}"}), 500


def process_recommendations(menu_data, user_recommendations):
    recommended_menu_dict = defaultdict(list)
    remaining_menu_dict = defaultdict(list)
    recommended_ids = {item_id for item_id, _ in user_recommendations}

    for item_id, name, category, price in menu_data:
        if str(item_id) in recommended_ids:
            recommended_menu_dict[category].append((name, price))
        else:
            remaining_menu_dict[category].append((name, price))
    
    return recommended_menu_dict, remaining_menu_dict


def construct_menu_response(username, recommended_menu_dict, remaining_menu_dict):
    fulfillment_text = f"Here is the menu based on your order history, {username}:\n"
    for category, items in recommended_menu_dict.items():
        fulfillment_text += f"\n{category}:\n"
        for name, price in items:
            fulfillment_text += f"- {name}: ${price}\n"

    fulfillment_text += "\n\nHere are the remaining items in the menu:\n"
    for category, items in remaining_menu_dict.items():
        fulfillment_text += f"\n{category}:\n"
        for name, price in items:
            fulfillment_text += f"- {name}: ${price}\n"

    return jsonify({'fulfillmentText': fulfillment_text})


def get_top_n_recommendations(algo, trainset, username, n=5):
    
    
    items_not_rated = [item_id for item_id in trainset.all_items() if item_id not in trainset.ur[trainset.to_inner_uid(username)]]


    predictions = [algo.predict(username, trainset.to_raw_iid(item_id)) for item_id in items_not_rated]


    predictions.sort(key=lambda x: x.est, reverse=True)


    return [(prediction.iid, round(float(prediction.est), 2)) for prediction in predictions[:n]]


def generate_normal_menu(menu_data):
    menu_dict = defaultdict(list)
    for item_id, name, category, price in menu_data:
        menu_dict[category].append((name, price))

    fulfillment_text = "Here is the full menu:\n"
    for category, items in menu_dict.items():
        fulfillment_text += f"\n{category}:\n"
        for name, price in items:
            fulfillment_text += f"- {name}: ${price}\n"

    return jsonify({'fulfillmentText': fulfillment_text})

if __name__ == '__main__':
    app.run(debug=True)