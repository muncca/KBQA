#!/usr/bin/env python
# -*- coding: utf-8 -*-

'''
Created on Aug 24, 2018

.. codeauthor: svitlana vakulenko
    <svitlana.vakulenko@gmail.com>

Baseline neural network architecture for KBQA

Based on https://github.com/DSTC-MSR-NLP/DSTC7-End-to-End-Conversation-Modeling/blob/master/baseline/baseline.py 

Question - text as the sequence of words (word embeddings index)
Answer - entity vector from KB (entity embeddings index)

'''
import sys
import os
import wget
import zipfile
import json

import random
import numpy as np

import scipy.sparse as sp
from sklearn.metrics.pairwise import cosine_similarity

from keras.models import Model
from keras.models import load_model

from keras.layers import Input, GRU, Dropout, Embedding, Dense, Flatten, Concatenate, concatenate
from keras.regularizers import l2
from keras.optimizers import Adam

from keras import backend as K

from keras.preprocessing.text import text_to_word_sequence
from keras.preprocessing.sequence import pad_sequences

from keras.callbacks import  ModelCheckpoint, EarlyStopping

from toy_data import *

from utils import *

# rdf2vec embeddings 200 dimensions
KB_EMBEDDINGS_PATH = "/data/globalRecursive/data.dws.informatik.uni-mannheim.de/rdf2vec/models/DBpedia/2016-04/GlobalVectors/11_pageRankSplit/DBpediaVecotrs200_20Shuffle.txt"
# subset of the KB embeddings (rdf2vec embeddings 200 dimensions from KB_EMBEDDINGS_PATH) for the entities of the LC-Quad dataset (both train and test split)
# selectedEmbeddings_KGlove_PR_Split_lcquad_answers_train_1_test_all.txt
LCQUAD_KB_EMBEDDINGS_PATH = "./data/selectedEmbeddings_rdf2vec_uniform_lcquad_answers_train_1_test_all.txt"


def create_KB_input(embeddings):
    '''
    Create an input which can be used in the network based on the existing embeddings
    '''
    from keras import backend as K
    k_constants = K.variable(embeddings)
    fixed_input = Input(tensor=k_constants)
    return fixed_input

def load_KB_embeddings(KB_embeddings_file=KB_EMBEDDINGS_PATH):
    '''
    load all embeddings from file
    '''
    entity2vec = {}

    print("Loading embeddings...")
    
    idx = 0
    entity2index = {}  # map from a token to an index
    index2entity = {}  # map from an index to a token 

    with open(KB_embeddings_file) as embs_file:
        # embeddings in a text file one per line for Global vectors and glove word embeddings
        for line in embs_file:
            entityAndVector = line.split(None, 1)
            # match the entity labels in vector embeddings
            entity = entityAndVector[0][1:-1]  # Dbpedia global vectors strip <> to match the entity labels
            try:
                embedding_vector = np.asarray(entityAndVector[1].split(), dtype='float32')
            except:
                print entityAndVector

            idx += 1  # 0 is reserved for masking in Keras
            entity2index[entity] = idx
            index2entity[idx] = entity
            entity2vec[entity] = embedding_vector
            n_dimensions = len(embedding_vector)

    print("Loaded %d embeddings with %d dimensions" % (len(entity2vec), n_dimensions))

    return (entity2index, index2entity, entity2vec, n_dimensions)


class KBQA:
    '''
    Baseline neural network architecture for KBQA
    '''
    def __init__(self, max_seq_len, rnn_units, encoder_depth, decoder_depth, num_hidden_units, bases, l2norm, n_negative_samples, dropout_rate=0.2, model_path='./models/model.best.hdf5'):
        self.max_seq_len = max_seq_len
        self.rnn_units = rnn_units
        self.encoder_depth = encoder_depth
        self.decoder_depth = decoder_depth
        self.num_hidden_units = num_hidden_units
        self.bases = bases
        self.l2norm = l2norm
        self.dropout_rate = dropout_rate
        makedirs('./models')
        self.model_path = model_path
        # load word vocabulary
        self.wordToIndex, self.indexToWord, self.wordToGlove = readGloveFile()
        self.n_negative_samples = n_negative_samples

    def _stacked_rnn(self, rnns, inputs, initial_states=None):
        # if initial_states is None:
        #     initial_states = [None] * len(rnns)
        # outputs, state = rnns[0](inputs, initial_state=initial_states[0])
        outputs = rnns[0](inputs)
        # states = [state]
        for i in range(1, len(rnns)):
            # outputs, state = rnns[i](outputs, initial_state=initial_states[i])
            outputs = rnns[i](outputs)
            # states.append(state)
        return outputs

    def create_pretrained_embedding_layer(self, isTrainable=True):
        '''
        Create pre-trained Keras embedding layer
        '''
        self.word_vocab_len = len(self.wordToIndex) + 1  # adding 1 to account for masking
        embeddings_matrix = load_embeddings_from_index(self.wordToGlove, self.wordToIndex)

        embeddingLayer = Embedding(self.word_vocab_len, embeddings_matrix.shape[1], weights=[embeddings_matrix], trainable=isTrainable, name='word_embedding', mask_zero=True)
        return embeddingLayer

    def build_model_train(self):
        '''
        build layers required for training the NN
        '''
        # Q - question input
        question_input = Input(shape=(None,), name='question_input')

        # I - positive/negative sample indicator (1/-1)
        sample_indicator = Input(shape=(1,), name='sample_indicator')

        # E' - question words embedding
        word_embedding = self.create_pretrained_embedding_layer()
        
        # Q' - question encoder
        question_encoder_output_1 = GRU(self.rnn_units, name='question_encoder_1', return_sequences=True)(word_embedding(question_input))
        question_encoder_output_2 = GRU(self.rnn_units, name='question_encoder_2', return_sequences=True)(question_encoder_output_1)
        question_encoder_output_3 = GRU(self.rnn_units, name='question_encoder_3', return_sequences=True)(question_encoder_output_2)
        question_encoder_output_4 = GRU(self.rnn_units, name='question_encoder_4', return_sequences=True)(question_encoder_output_3)
        question_encoder_output = GRU(self.kb_embeddings_dimension, name='question_encoder')(question_encoder_output_4)

        print("%d samples of max length %d with %d hidden layer dimensions"%(self.num_samples, self.max_seq_len, self.rnn_units))
        
        # answer_output = Dropout(self.dropout_rate)(question_encoder_output)
        answer_output = question_encoder_output

        answer_indicator_output = Concatenate(axis=1)([answer_output, sample_indicator])
        # answer_indicator_output = concatenate([answer_output, sample_indicator], axis=0)

        self.model_train = Model(inputs=[question_input, sample_indicator],   # [input question, input KB],
                                 outputs=[answer_indicator_output])                        # ground-truth target answer
        print self.model_train.summary()

    def load_data(self, dataset, split):
        questions, answers = dataset
        assert len(questions) == len(answers)

        # encode questions and answers using embeddings vocabulary
        num_samples = len(questions)
        self.entities = self.entity2vec.keys()

        questions_data = []
        answers_data = []
        answers_indices = []
        samples_indicators = []
        not_found_entities = 0

        # iterate over samples
        for i in range(num_samples):
            # encode words (ignore OOV words)
            questions_sequence = [self.wordToIndex[word] for word in text_to_word_sequence(questions[i]) if word in self.wordToIndex]
            answers_to_question = answers[i]
            
            if split == 'train':
                # train only on the first answer from the answer set
                first_answer = answers_to_question[0].encode('utf-8')
                # filter out answers without pre-trained embeddings
                if first_answer in self.entities:
                    # TODO match unicode lookup
                    questions_data.append(questions_sequence)
                    answers_data.append(self.entity2vec[first_answer])

                    # generate a random negative sample for each positive sample
                    # pick n random entities
                    for i in range(self.n_negative_samples):
                        questions_data.append(questions_sequence)
                        random_entity = random.choice(self.entities)
                        answers_data.append(self.entity2vec[random_entity])

                    samples_indicators.append(1)
                    samples_indicators.extend([-1] * self.n_negative_samples)

            if split == 'test':
                # add all answer indices for testing
                answer_indices = []
                for answer in answers_to_question:
                    answer = answer.encode('utf-8')
                    if answer in self.entity2vec.keys():
                        answer_indices.append(self.entity2index[answer])

                answers_indices.append(answer_indices)
                # if answer_indices:
                questions_data.append(questions_sequence)
                samples_indicators.append(1)


            # else:
            #     not_found_entities +=1
        print("Samples indicators: %d" % len(samples_indicators))
        
        print ("Not found: %d entities"%not_found_entities)
        # normalize length
        questions_data = np.asarray(pad_sequences(questions_data, padding='post'))
        print("Maximum question length %d"%questions_data.shape[1])
        answers_data = np.asarray(answers_data)
        samples_indicators = np.asarray(samples_indicators)

        self.num_samples = questions_data.shape[0]

        
       
        # print questions_data
        # print answers_data

        self.dataset = (questions_data, answers_data, answers_indices, samples_indicators)
        print("Loaded the dataset")

    def load_pretrained_model(self):
        self.model_train = load_model(self.model_path, custom_objects={'loss': self.samples_loss()})

    def samples_loss(self):
        def loss(y_true, y_pred):
            print ("Predicted vectors: %s" % str(y_pred.shape))
            y_true = K.l2_normalize(y_true, axis=-1)

            y_indicator = y_pred[:,-1]
            print ("Indicators vector: %s" % str(y_indicator.shape))

            y_pred = y_pred[:,:-1]
            print ("Predicted vectors: %s" % str(y_pred.shape))


            y_pred = K.l2_normalize(y_pred, axis=-1)
            # y_indicator = K.print_tensor(y_indicator, message="y_indicator vector")
            
            loss_vector = -K.sum(y_true * y_pred, axis=-1) * y_indicator
            # loss_vector = K.print_tensor(loss_vector, message="loss vector")
            return loss_vector
        return loss

    def train(self, batch_size, epochs, batch_per_load=10, lr=0.001):
        self.model_train.compile(optimizer=Adam(lr=lr), loss=self.samples_loss())
        questions_vectors, answers_vectors, answers_indices, samples_indicators = self.dataset
        
        # early stopping
        checkpoint = ModelCheckpoint(self.model_path, monitor='val_loss', verbose=1, save_best_only=True, mode='min')
        early_stop = EarlyStopping(monitor='val_loss', patience=5, mode='min') 
        callbacks_list = [checkpoint, early_stop]
        self.model_train.fit([questions_vectors, samples_indicators], [answers_vectors], epochs=epochs, callbacks=callbacks_list, verbose=2, validation_split=0.3, shuffle='batch')

    def test(self):
        questions_vectors, answers_vectors, answers_indices, sample_indicators = self.dataset
        print("Testing...")
        # score = self.model_train.evaluate(questions, answers, verbose=0)
        # print score
        print("Questions vectors shape: " + " ".join([str(dim) for dim in questions_vectors.shape]))
        # print("Answers vectors shape: " + " ".join([str(dim) for dim in answers_vectors.shape]))
        print("Answers indices shape: %d" % len(answers_indices))

        predicted_answers_vectors = self.model_train.predict([questions_vectors, sample_indicators])[:,:-1]
        print("Predicted answers vectors shape: " + " ".join([str(dim) for dim in predicted_answers_vectors.shape]))
        # print("Answers indices: " + ", ".join([str(idx) for idx in answers_indices]))

        # load embeddings into matrix
        embeddings_matrix = load_embeddings_from_index(self.entity2vec, self.entity2index)
        # calculate pairwise distances (via cosine similarity)
        similarity_matrix = cosine_similarity(predicted_answers_vectors, embeddings_matrix)

        # print np.argmax(similarity_matrix, axis=1)

        n = 5
        # indices of the top n predicted answers for every question in the test set
        top_ns = similarity_matrix.argsort(axis=1)[:, -n:][::-1]
        # print top_ns[:2]

        hits = 0
        for i, answers in enumerate(answers_indices):
            # check if the correct and predicted answer sets intersect
            if set.intersection(set(answers), set(top_ns[i])):
            # if set.intersection(set([answers[0]]), set(top_ns[i])):
                hits += 1

        print("Hits in top %d: %d/%d"%(n, hits, len(answers_indices)))


def load_lcquad(dataset_split):
    # load embeddings
    entity2index, index2entity, entity2vec, kb_embeddings_dimension = load_KB_embeddings(LCQUAD_KB_EMBEDDINGS_PATH)
    QS = []
    AS = []
    with open("./data/lcquad_%s.json"%dataset_split, "r") as train_file:
        qas = json.load(train_file)
        for qa in qas:
            QS.append(qa['question'])
            AS.append(qa['answers'])
    return (QS, AS), entity2index, index2entity, entity2vec, kb_embeddings_dimension


# def load_dbnqa():
#     return (QS, AS)


def load_toy_data():
    return (QS, AS), ENTITY2VEC, KB_EMBEDDINGS_DIM


def train_model(model, epochs, batch_size, learning_rate):
    '''
    dataset_name <String> Choose one of the available datasets to train the model on ('toy', 'lcquad')
    '''
    # build model
    model.build_model_train()
    # train model
    model.train(batch_size, epochs, lr=learning_rate)


def test_model(model):
    '''
    dataset_name <String> Choose one of the available datasets to test the model on ('lcquad')
    '''
    model.load_pretrained_model()
    print("Loaded the pre-trained model")
    model.test()


def load_dataset(model, dataset_name, mode):
    print("Loading %s..."%dataset_name)
    
    if dataset_name == 'toy':
        dataset, model.entity2vec, model.kb_embeddings_dimension = load_toy_data()
    # elif dataset_name == 'dbnqa':
    #     dataset = load_dbnqa()
    elif dataset_name == 'lcquad':
        dataset, model.entity2index, model.index2entity, model.entity2vec, model.kb_embeddings_dimension = load_lcquad(mode)

    model.load_data(dataset, mode)


def main(mode):
    # set mode and dataset
    # mode = 'test'
    dataset_name = 'lcquad'
    # dataset_name = 'lcquad_test'

    # define QA model architecture parameters
    max_seq_len = 10
    rnn_units = 500  # dimension of the GRU output layer (hidden question representation) 
    encoder_depth = 2
    decoder_depth = 2
    dropout_rate = 0.5

    # define R-GCN architecture parameters
    num_hidden_units = 16
    bases = -1
    l2norm = 0.

    # define training parameters
    batch_size = 100
    epochs = 20  # 10
    learning_rate = 1e-3
    n_negative_samples = 1

    # initialize the model
    model = KBQA(max_seq_len, rnn_units, encoder_depth, decoder_depth, num_hidden_units, bases, l2norm, n_negative_samples, dropout_rate)

    # load data
    load_dataset(model, dataset_name, mode)
    
    # modes
    if mode == 'train':
        train_model(model, epochs, batch_size, learning_rate)
    elif mode == 'test':
        test_model(model)


if __name__ == '__main__':
    set_random_seed()
    main(sys.argv[1])