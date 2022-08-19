import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import seaborn as sns
from tensorflow.keras.layers import LeakyReLU
from tensorflow.keras.models import load_model
from sklearn.metrics import accuracy_score, confusion_matrix, recall_score, f1_score, precision_score
from tensorflow_addons.optimizers import Lookahead
from tensorflow.keras.optimizers import Adam

# In[1]:
# See Extractor.py
print("Loading Features...")

path = '/smallwork/m.hackett_local/data/USignite/features/'
file = 'usignite_features.csv'
df = pd.read_csv(path+file)
df.info()

features = df.iloc[0:,:-1].columns
target = ['Anomaly']

X = df[features]
y = df[target]

# Remove original anomalous subflows (if any)
clean_indices = y[y['Anomaly'] == 0].index
X_clean = X.loc[clean_indices]

# In[2]:

# Anomaly generator
def generate_mal_subflows(num_mal, pkts_sec, pkt_size, gradient):
    mal_subflows = []
    for i in range(num_mal):
        mal_subflows.append(mal_subflow(pkts_sec, pkt_size, gradient))
    mal_subflows = pd.DataFrame(mal_subflows)
    X_clean['Anomaly'] = 0
    mal_subflows.columns = X_clean.columns
    # Concatenate normal subflows and malicious subflows
    data = [X_clean, mal_subflows]
    return pd.concat(data).sample(frac=1)

def mal_subflow(pkts_sec, pkt_size, gradient):
    mal_features = []
    dur = 5
    num_pkts = pkts_sec * dur
    mal_features.append(pkts_sec)
    # Simple ICMP flood with same size packets (bytes)
    if gradient:
        pkt_size = np.random.randint(64, pkt_size)
    total_size = pkt_size * num_pkts
    total_size /= 1e3 # KB
    bits_sec = (total_size * 8)/dur
    mal_features.append(bits_sec)
    mal_features.append(pkt_size) 
    mal_features.append(0) # no standard deviation for uniform sizes
    for i in range(5):
        mal_features.append(pkt_size)
    mal_features.append(0) # No TCP flags for ICMP 
    mal_features.append(128) # Arbitrary avg TTL
    mal_features.append(1) # Mark as anomaly
    return pd.Series(mal_features)

# Anomalous Flow Generation
pkt_size = 500 # Nominal range: 40 - 1389 bytes (65,535 bytes max in IPv4)
pkts_sec = 20 # Highest in nominal data: 14
# Generate each flow using different packet size (between 64 and pkt_size)
# Note: Packet sizes are equal in each subflow, not randomized
gradient = True
# Number of malicious subflows to generate
num_mal = np.ceil(X_clean.shape[0] / 5).astype(int) # 20% of clean subflows

dirty_subflows = generate_mal_subflows(num_mal, pkts_sec, pkt_size, gradient)

X = dirty_subflows[features]
y = dirty_subflows[target]


# In[3]:

# Load best model and find threshold

model_name = "autoencoder_model_1.tf"

# MODELS 1-4
autoencoder = load_model(model_name)

# MODELS 5-8
#autoencoder = load_model(model_name, custom_objects={"opt":Lookahead(Adam())})

# MODELS 9-12
#autoencoder = load_model(model_name, custom_objects={"act1": LeakyReLU(), "act2": LeakyReLU()})

# MODELS 13-16
#autoencoder = load_model(model_name, custom_objects={"act1": LeakyReLU(), "act2": LeakyReLU(), "opt":Lookahead(Adam())})

X_pred_clean = autoencoder.predict(X_clean[features])
clean_mae_loss = np.mean(np.abs(X_pred_clean - X_clean[features]), axis=1)
threshold = np.max(clean_mae_loss)
print("Reconstuction error threshold: ", threshold)

# Calculate threshold by accounting for standard deviation
mean = np.mean(clean_mae_loss, axis=0)
sd = np.std(clean_mae_loss, axis=0)
num_sd = 3

# '2*sd' = ~97.5%, '1.76 = ~96%', '1.64 = ~95%'
final_list = [x for x in clean_mae_loss if (x > mean - num_sd * sd)] 
final_list = [x for x in final_list if (x < mean + num_sd * sd)]
sd_threshold = np.max(final_list)
print("max value after removing 3*std:", sd_threshold)
print("number of packets removed:", (len(clean_mae_loss) - len(final_list)))
print("number of packets before removal:", len(clean_mae_loss))

# Graph depicts threshold line and location of normal and malicious data
X_pred = autoencoder.predict(X) 
test_mae_loss = np.mean(np.abs(X_pred - X), axis=1)
 
data = [test_mae_loss, y]
error_df_test = pd.concat(data, axis=1)
error_df_test.columns=['Reconstruction_error','True_class']

error_df_test = error_df_test.reset_index()

groups = error_df_test.groupby('True_class')
fig, ax = plt.subplots()

for name, group in groups:
    ax.plot(group.index, group.Reconstruction_error, 
            marker='o', ms=3.5, linestyle='', 
            label= "Anomaly" if name == 1 else "Normal") 
ax.hlines(sd_threshold, ax.get_xlim()[0], ax.get_xlim()[1], colors="r", zorder=100, label='Threshold')
    
ax.legend()
plt.title("Reconstruction error for different classes")
plt.ylabel("Reconstruction error")
plt.xlabel("Data point index")
plt.show()

#Confusion Matrix heat map

pred_y = [1 if e > sd_threshold else 0 for e in error_df_test['Reconstruction_error'].values]
conf_matrix = confusion_matrix(error_df_test['True_class'], pred_y) 
plt.figure(figsize=(8, 6))
sns.heatmap(conf_matrix,
            xticklabels=["Normal","Anomaly"], 
            yticklabels=["Normal","Anomaly"], 
            annot=True, fmt="d");
plt.title("Confusion matrix")
plt.ylabel('True class')
plt.xlabel('Predicted class')
plt.show()

#   TN | FP
#   -------
#   FN | TP

print(" accuracy:  ", accuracy_score(error_df_test['True_class'], pred_y))
print(" recall:    ", recall_score(error_df_test['True_class'], pred_y))
print(" precision: ", precision_score(error_df_test['True_class'], pred_y))
print(" f1-score:  ", f1_score(error_df_test['True_class'], pred_y))

# In[4]:
# Print Commands
def ae_stats(properties):
    weight_index = 0
    # Test if decoder is transposed or normal
    decoder_test = len(autoencoder.layers[2].get_weights()[0])
    if decoder_test == 10:
        weight_index = 1
    # Decoder weights
    hdec_weights = autoencoder.layers[2].get_weights()[weight_index]
    odec_weights = autoencoder.layers[3].get_weights()[weight_index]
    # For dense transposed layers
    if weight_index == 1: 
        hdec_weights = hdec_weights.T 
        odec_weights = odec_weights.T
    if 'weights' in properties:
        print('===Encoder weights===')
        print("Hidden layer")
        print(np.round(np.transpose(autoencoder.layers[0].get_weights()[0]), 3), end="\n\n")
        print("Latent layer")
        print(np.round(np.transpose(autoencoder.layers[1].get_weights()[0]), 3), end="\n\n")
        print('===Decoder weights===')
        print("Hidden layer 2")
        print(np.round(hdec_weights, 3), end="\n\n")
        print("Output layer")
        print(np.round(odec_weights, 3), end="\n\n")
    if 'norm' in properties:
        print('Encoder weights norm')
        w_encoder = np.round(autoencoder.layers[0].get_weights()[0], 2).T
        print(np.round(np.sum(w_encoder ** 2, axis = 1),3), end="\n\n")
        w_encoder = np.round(autoencoder.layers[1].get_weights()[0], 2).T
        print(np.round(np.sum(w_encoder ** 2, axis = 1),3), end="\n\n")
        print('Decoder weights norm')
        w_decoder = np.round(hdec_weights, 2)  
        print(np.round(np.sum(w_decoder ** 2, axis = 1),3), end="\n\n")
        w_decoder = np.round(odec_weights, 2) 
        print(np.round(np.sum(w_decoder ** 2, axis = 1),3), end="\n\n")
    if 'ortho' in properties:
        print('Encoder weights dot products')
        w_encoder = autoencoder.layers[0].get_weights()[0]
        print(np.round(np.dot(w_encoder.T, w_encoder), 2), end="\n\n")
        w_encoder = autoencoder.layers[1].get_weights()[0]
        print(np.round(np.dot(w_encoder.T, w_encoder), 2), end="\n\n")
        print('Decoder weights dot product')
        w_decoder = autoencoder.layers[2].get_weights()[weight_index]
        print(np.round(np.dot(w_decoder.T, w_decoder), 2), end="\n\n")
        w_decoder = autoencoder.layers[3].get_weights()[weight_index]
        print(np.round(np.dot(w_decoder.T, w_decoder), 2), end="\n\n")
# ['weights', 'norm', 'ortho']
properties = ['weights', 'norm', 'ortho']
ae_stats(properties)