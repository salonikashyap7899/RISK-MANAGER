def calculate_position(capital, sl_points):
if sl_points <= 0:
return 0


risk = capital * 0.01 # 1% risk
return risk / (sl_points + 20)




def calculate_targets(data):
return {
"tp1": data["tp1_points"],
"tp2": data["tp2_points"],
"tp3": data["tp3_points"],
"tp1_percent": data["tp1_percent"],
"tp2_percent": data["tp2_percent"],
"tp3_percent": data["tp3_percent"],
}