# coding=utf-8
# Author: Kim Hammar <kimham@kth.se> KTH 2018

import gensim
import json
import urllib
from nltk.metrics import edit_distance
from nltk.stem import WordNetLemmatizer
import math
import lda
from sklearn.feature_extraction.text import CountVectorizer
import numpy as np
import re
from collections import Counter
import wikipedia
from googlesearch.googlesearch import GoogleSearch
from selenium import webdriver
from bs4 import BeautifulSoup
from deepomatic import Client
from time import sleep
from dd_client import DD
import io
import os
from clarifai.rest import ClarifaiApp
from clarifai.rest import Image as ClImage
from google.cloud import vision
from google.cloud.vision import types

class InformationExtractor(object):
    """ Module with functions for information Extraction """
    wordnet_lemmatizer = WordNetLemmatizer()

    #External service URLs
    google_service_url = 'https://kgsearch.googleapis.com/v1/entities:search'
    probase_service_url = "https://concept.research.microsoft.com/api/Concept/ScoreByProb"
    #DD constants
    height = width = 224
    nclasses_clothing = 304
    nclasses_bags = 37
    nclasses_footwear = 51
    nclasses_fabric= 233

    #setting up DD client
    mllib = 'caffe'

    def __init__(self, word_vectors, companies, styles, materials, items, probase_brands,
                 probase_materials, patterns, top_category_items, deep_detectStartup, confFilePath, tfidf):
        self.conf = json.load(open(confFilePath))
        self.tfidf = tfidf
        self.api_key = self.conf["google_api_key_path"]
        self.deep_detect_models = self.conf["deep_detect_models"]
        self.CAPTION_FACTOR = self.conf["caption_factor"]
        self.COMMENTS_FACTOR = self.conf["comments_factor"]
        self.USERTAG_FACTOR = self.conf["usertag_factor"]
        self.HASHTAG_FACTOR = self.conf["hashtag_factor"]
        if deep_detectStartup:
            self.dd = DD(self.conf["deep_detect_host"], port=self.conf["deep_detect_port"])
            self.startup_deep_detect()
        self.wordvec_model = gensim.models.KeyedVectors.load_word2vec_format(word_vectors, binary=False)
        self.companies = companies
        self.styles = styles
        self.materials = materials
        self.items = items
        self.brands_keywords_google = []
        self.materials_keywords_google = []
        self.probase_brands = probase_brands
        self.probase_materials = probase_materials
        self.colors = []
        self.patterns = patterns
        self.top_category_items=top_category_items
        self.lemmatize()

    def lemmatize(self):
        """ Lemmatize domain lists"""
        self.styles_lemmas = {self.wordnet_lemmatizer.lemmatize(style): style for style in self.styles}
        self.materials_lemmas = {self.wordnet_lemmatizer.lemmatize(material): material for material in self.materials}
        self.items_lemmas = {self.wordnet_lemmatizer.lemmatize(item): item for item in self.items}

    def find_closest_semantic(self, caption, comments, tags, hashtags, segmented_hashtags, num, topic, id):
        """ Finds num semantically closest candidates for a given topic"""
        topic = map(lambda x: x.decode('utf-8','ignore').encode("utf-8"), topic)
        freq_scores = {}
        for x in topic:
            freq_scores[x] = 0.0
        for token in caption:
            scores = []
            for x in topic:
                token2 = x.lower()
                token2Lemma = self.wordnet_lemmatizer.lemmatize(token2)
                similarity = self.token_similarity(token, token2, token2Lemma, self.CAPTION_FACTOR, self.tfidf[id])
                scores.append((x, similarity))
            top = sorted(scores, reverse=True, key=lambda x: x[1])[:num]
            for x in top:
                freq_scores[x[0]] = freq_scores[x[0]] + x[1]
        for token in comments:
            scores = []
            for x in topic:
                token2 = x.lower()
                token2Lemma = self.wordnet_lemmatizer.lemmatize(token2)
                similarity = self.token_similarity(token, token2, token2Lemma, self.COMMENTS_FACTOR, self.tfidf[id])
                scores.append((x, similarity))
            top = sorted(scores, reverse=True, key=lambda x: x[1])[:num]
            for x in top:
                freq_scores[x[0]] = freq_scores[x[0]] + x[1]
        for token in hashtags:
            scores = []
            for x in topic:
                token2 = x.lower()
                token2Lemma = self.wordnet_lemmatizer.lemmatize(token2)
                similarity = self.token_similarity(token, token2, token2Lemma, self.HASHTAG_FACTOR, self.tfidf[id])
                scores.append((x, similarity))
            top = sorted(scores, reverse=True, key=lambda x: x[1])[:num]
            for x in top:
                freq_scores[x[0]] = freq_scores[x[0]] + x[1]
        for token in segmented_hashtags:
            scores = []
            for x in topic:
                token2 = x.lower()
                token2Lemma = self.wordnet_lemmatizer.lemmatize(token2)
                similarity = self.token_similarity(token, token2, token2Lemma, self.HASHTAG_FACTOR, self.tfidf[id])
                scores.append((x, similarity))
            top = sorted(scores, reverse=True, key=lambda x: x[1])[:num]
            for x in top:
                freq_scores[x[0]] = freq_scores[x[0]] + x[1]
        for token in tags:
            scores = []
            for x in topic:
                token2 = x.lower()
                token2Lemma = self.wordnet_lemmatizer.lemmatize(token2)
                similarity = self.token_similarity(token, token2, token2Lemma, self.USERTAG_FACTOR, self.tfidf[id])
                scores.append((x, similarity))
            top = sorted(scores, reverse=True, key=lambda x: x[1])[:num]
            for x in top:
                freq_scores[x[0]] = freq_scores[x[0]] + x[1]
        top = sorted([(k, v) for k, v in freq_scores.iteritems()], reverse=True, key=lambda x: x[1])[:num]
        return top

    def token_similarity(self, token, token2, token2Lemma, factor, tfidf):
        """ Returns similarity between two tokens using cosine similarity between embeddings, edit distance and TFIDF weighting"""
        similarity = 0.0
        if isinstance(token, str):
            token = token.decode("utf-8", "ignore")
        tokenLemma = self.wordnet_lemmatizer.lemmatize(token)
        if tokenLemma in self.wordvec_model.wv.vocab and token2Lemma in self.wordvec_model.wv.vocab:
            if edit_distance(tokenLemma, token2Lemma) == 0:
                factor = factor*10
            similarity = factor*math.pow(float(self.wordvec_model.wv.similarity(tokenLemma, token2Lemma)), 2)
        else:
            dist = factor*edit_distance(tokenLemma, token2Lemma)
            similarity = float(1)/float(1 + math.pow(dist, 2))
        tfidf_score = 0.0
        if token in tfidf:
            tfidf_score = tfidf[token]
        if token.encode("utf-8") in tfidf:
            tfidf_score = tfidf[token.encode("utf-8")]
        tfidf_score = max(tfidf_score, 0.0001)
        similarity = similarity*tfidf_score
        return similarity

    def find_closest_syntactic(self, caption, comments, tags, hashtags, segmented_hashtags, num, topic, id):
        """ Finds num semantically closest candidates for a given topic"""
        topic = map(lambda x: x.decode('utf-8','ignore').encode("utf-8"), topic)
        freq_scores = {}
        for x in topic:
            freq_scores[x] = 0.0
        for token in caption:
            scores = []
            for x in topic:
                token2 = x.lower()
                token2Lemma = self.wordnet_lemmatizer.lemmatize(token2)
                similarity = self.token_similarity_syntactic_only(token, token2, token2Lemma, self.CAPTION_FACTOR, self.tfidf[id])
                scores.append((x, similarity))
            top = sorted(scores, reverse=True, key=lambda x: x[1])[:num]
            for x in top:
                freq_scores[x[0]] = freq_scores[x[0]] + x[1]
        for token in comments:
            scores = []
            for x in topic:
                token2 = x.lower()
                token2Lemma = self.wordnet_lemmatizer.lemmatize(token2)
                similarity = self.token_similarity_syntactic_only(token, token2, token2Lemma, self.COMMENTS_FACTOR, self.tfidf[id])
                scores.append((x, similarity))
            top = sorted(scores, reverse=True, key=lambda x: x[1])[:num]
            for x in top:
                freq_scores[x[0]] = freq_scores[x[0]] + x[1]
        for token in hashtags:
            scores = []
            for x in topic:
                token2 = x.lower()
                token2Lemma = self.wordnet_lemmatizer.lemmatize(token2)
                similarity = self.token_similarity_syntactic_only(token, token2, token2Lemma, self.HASHTAG_FACTOR, self.tfidf[id])
                scores.append((x, similarity))
            top = sorted(scores, reverse=True, key=lambda x: x[1])[:num]
            for x in top:
                freq_scores[x[0]] = freq_scores[x[0]] + x[1]
        for token in segmented_hashtags:
            scores = []
            for x in topic:
                token2 = x.lower()
                token2Lemma = self.wordnet_lemmatizer.lemmatize(token2)
                similarity = self.token_similarity_syntactic_only(token, token2, token2Lemma, self.HASHTAG_FACTOR, self.tfidf[id])
                scores.append((x, similarity))
            top = sorted(scores, reverse=True, key=lambda x: x[1])[:num]
            for x in top:
                freq_scores[x[0]] = freq_scores[x[0]] + x[1]
        for token in tags:
            scores = []
            for x in topic:
                token2 = x.lower()
                token2Lemma = self.wordnet_lemmatizer.lemmatize(token2)
                similarity = self.token_similarity_syntactic_only(token, token2, token2Lemma, self.USERTAG_FACTOR, self.tfidf[id])
                scores.append((x, similarity))
            top = sorted(scores, reverse=True, key=lambda x: x[1])[:num]
            for x in top:
                freq_scores[x[0]] = freq_scores[x[0]] + x[1]
        top = sorted([(k, v) for k, v in freq_scores.iteritems()], reverse=True, key=lambda x: x[1])[:num]
        return top

    def token_similarity_syntactic_only(self, token, token2, token2Lemma, factor, tfidf):
        """ Returns similarity between two tokens using edit distance and TFIDF weighting"""
        tokenLemma = self.wordnet_lemmatizer.lemmatize(token)
        similarity = 0.0
        if edit_distance(tokenLemma, token2Lemma) == 0:
            factor = factor*10
        dist = edit_distance(tokenLemma, token2Lemma)
        similarity = factor*(float(1)/float(1 + dist))
        tfidf_score = 0.0
        if token in tfidf:
            tfidf_score = tfidf[token]
        if token.encode("utf-8") in tfidf:
            tfidf_score = tfidf[token.encode("utf-8")]
        tfidf_score = max(tfidf_score, 0.0001)
        similarity = similarity*tfidf_score
        return similarity


    def lookup_google(self, params):
        """ Lookup in Google Search"""
        #curl "https://kgsearch.googleapis.com/v1/entities:search?query=bebe&key=<key>&limit=2&indent=True&types=Organization"
        url = self.google_service_url + '?' + urllib.urlencode(params)
        #result score = an indicator of how well the entity matched the request constraints.
        response = json.loads(urllib.urlopen(url).read())
        results = []
        if "itemListElement" in response:
            for element in response['itemListElement']:
                dict_result = {}
                if "resultScore" in element:
                    dict_result["resultScore"] = element['resultScore']
                if "result" in element:
                    if "detailedDescription" in element["result"]:
                        dict_result["detailedDescription"] = element["result"]['detailedDescription']
                    if "description" in element["result"]:
                        dict_result["description"] = element["result"]['description']
                    if "url" in element["result"]:
                        dict_result["url"] = element["result"]["url"]
                results.append(dict_result)
        return results

    def rank_google_result_company(self, results):
        """ Binary rank  of google search results"""
        for result in results:
            for keyword in self.brands_keywords_google:
                if "detailedDescription" in result:
                    if keyword in result["detailedDescription"]:
                        return 1
                if "description" in result:
                    if keyword in result["description"]:
                        return 1
        return 0.0

    def rank_google_result_material(self, results):
        """ Binary rank  of google search results"""
        for result in results:
            for keyword in self.materials_keywords_google:
                if keyword in result["detailedDescription"] or keyword in result["description"]:
                    return 1
        return 0.0

    def rank_probase_result_company(self, result):
        """Probase probability ranking [0,1]"""
        keywords = filter(lambda x: x in result, self.probase_brands)
        keywords = map(lambda x: result[x], keywords)
        if len(keywords) > 0:
            return 1 + max(keywords)
        else:
            return 0.5

    def rank_probase_result_material(self, result):
        """Probase probability ranking [0,1]"""
        keywords = filter(lambda x: x in result, self.probase_materials)
        keywords = map(lambda x: result[x], keywords)
        if len(keywords) > 0:
            return 1 + max(keywords)
        else:
            return 0.5

    def lookup_probase(self, params):
        """Probase lookup"""
        #curl "https://concept.research.microsoft.com/api/Concept/ScoreByProb?instance=adidas&topK=10"
        url = self.probase_service_url + '?' + urllib.urlencode(params)
        response = json.loads(urllib.urlopen(url).read())
        return response

    def get_liketoknowitlinks(self, tokens):
        """ Extract liketoknowit links"""
        links = []
        for token in tokens:
            match = re.search("http://liketk.it/([^\s]+)", token)
            if match is not None:
                link = match.group(0)
                links.append(link)
        return links

    def lda_topic_models(self, num_topics, num_iter, min_occ, docs):
        """ Extract LDA topic models """
        cvectorizer = CountVectorizer(min_df=min_occ, stop_words="english")
        cvz = cvectorizer.fit_transform(docs)
        lda_model = lda.LDA(n_topics=num_topics, n_iter=num_iter)
        X_topics = lda_model.fit_transform(cvz)
        _lda_keys = []
        for i in xrange(X_topics.shape[0]):
            _lda_keys.append(X_topics[i].argmax())
        topic_summaries = []
        topic_word = lda_model.topic_word_  # all topic words
        n_top_words = 5
        vocab = cvectorizer.get_feature_names()
        for i, topic_dist in enumerate(topic_word):
            topic_words = np.array(vocab)[np.argsort(topic_dist)][:-(n_top_words + 1):-1] # get!
            topic_summaries.append(' '.join(topic_words))
        return topic_summaries

    def get_top_num(self, coll, num):
        """ Extract top 10 ranked items"""
        top, counts = zip(*Counter(coll).most_common(num))
        return list(top)

    def get_wikipedia_vote(self, query):
        """ Wikipedia lookup binary rank"""
        pages = wikipedia.search(query)
        for pageName in pages:
            try:
                page = wikipedia.page(pageName)
                content = page.content.lower()
                for keyword in self.brands_keywords_google:
                    if keyword in content:
                        return 1
            except:
                return 0.0
        return 0.0

    def get_google_search_vote(self, query):
        """ Google search lookup binary rank"""
        try:
            response = GoogleSearch().search(query)
            for result in response.results:
                text = result.getText().lower()
                title  = result.title.lower()
                for keyword in self.brands_keywords_google:
                    if keyword in text or keyword in title:
                        return 1
        except:
            return 0
        return 0

    def emoji_classification(self, emojis,num):
        """ Emoji classification """
        items = {}
        for item in self.items_lemmas.keys():
            items[item] = 0.0
        for emoji in emojis:
            item_matches = self.emoji_to_item(emoji)
            for item_m in item_matches:
                items[item_m] = items[item_m] + 1
        top = sorted([(k, v) for k, v in items.iteritems()], reverse=True, key=lambda x: x[1])[:num]
        return top

    def emoji_to_item(self, token):
        """Classify item based on emojis"""
        if token == u"👕":
            return ["shirt", "top"]
        if token == u"👖":
            return ["jean", "trouser", "legging", "jogger"]
        if token == u"👗":
            return ["dress"]
        if token == u"👚":
            return ["blouse", "shirt"]
        if token == u"👛":
            ["purse", "bag", "handbag"]
        if token == u"👜":
            return ["bag", "handbag"]
        if token == u"👝" or token == u"🎒 ":
            return ["bag"]
        if token == u"👞":
            return ["shoe", "boot"]
        if token == u"👟":
            return ["trainer", "shoe", "boot"]
        if token == u"👠" or token == u"👡 " or token == u"👢":
            return ["heel", "shoe"]
        if token == u"👒" or token == u"🎩":
            return ["hat"]
        return []

    def map_candidates_to_ontology(self, candidates):
        """ Map candidates from external APIs to our classes"""
        topic = map(lambda x: x.decode('utf-8','ignore').encode("utf-8"), self.top_category_items)
        freq_scores = {}
        for x in topic:
            parts = x.split(",")
            label = parts[0]
            freq_scores[label] = 0.0
        for token in candidates:
            for x in topic:
                parts = x.split(",")
                label = parts[0]
                words = parts[1].split(" ")
                acc_sim = 0
                scores = []
                for word in words:
                    token2 = word.lower()
                    token2Lemma = self.wordnet_lemmatizer.lemmatize(token2)
                    similarity = self.token_similarity(token[0], token2, token2Lemma, self.CAPTION_FACTOR)
                    scores.append(similarity*math.pow(token[1],2))

                acc_sim = acc_sim + max(scores)
                freq_scores[label] = freq_scores[label] + acc_sim
        return freq_scores

    def liketkit_classification(self, url):
        """ Liketkit link scraping """
        text = []
        try:
            driver = webdriver.PhantomJS()
            driver.get(url)
            p_element = driver.find_element_by_class_name("ltk-products")
            products = p_element.find_elements_by_xpath(".//*")
            urls = []
            for prod in products:
                urls.append(prod.get_attribute("href"))
            for url in urls:
                driver.get(url)
                html = driver.page_source
                soup = BeautifulSoup(html, "lxml")
                data = soup.findAll(text=True, recursive=True)
                text.extend(list(data))
                return text
        except:
            print("error in liketkit classification")
            return text

    def google_vision_lookup(self, imagePath):
        """ Google vision API lookup """
        item_candidates = []
        try:
            # Instantiates a client
            client = vision.ImageAnnotatorClient()

            # The name of the image file to annotate
            file_name = os.path.join(
                os.path.dirname(__file__),
                imagePath)

            # Loads the image into memory
            with io.open(file_name, 'rb') as image_file:
                content = image_file.read()

            image = types.Image(content=content)

            # Performs label detection on the image file
            response = client.label_detection(image=image)
            labels = response.label_annotations
            for label in labels:
                item_candidates.append((label.description, label.score))
            return item_candidates
        except:
            print("error in google_vision_LF")
            return item_candidates

    def deep_detect_lookup(self, link):
        """ Deep detect local lookup"""
        items_and_fabrics = {}
        items_and_fabrics["items"] = []
        items_and_fabrics["fabrics"] = []
        try:
            parameters_input = {}
            parameters_mllib = {}
            parameters_output = {'best':10}
            data = [link]
            clothing_res = self.dd.post_predict(self.sname_clothing,data,parameters_input,parameters_mllib,parameters_output)
            body = clothing_res[u"body"]
            predictions = body[u"predictions"]
            classes = predictions[0][u"classes"]
            for c in classes:
                items = c[u"cat"].strip(" ").split(",")
                prob = c[u"prob"]
                for item in items:
                    items_and_fabrics["items"].append((item, prob))

            bags_res = self.dd.post_predict(self.sname_bags,data,parameters_input,parameters_mllib,parameters_output)
            body = bags_res[u"body"]
            predictions = body[u"predictions"]
            classes = predictions[0][u"classes"]
            for c in classes:
                items = c[u"cat"].strip(" ").split(",")
                prob = c[u"prob"]
                for item in items:
                    items_and_fabrics["items"].append((item, 0.5*prob))

            footwear_res = self.dd.post_predict(self.sname_footwear,data,parameters_input,parameters_mllib,parameters_output)
            body = footwear_res[u"body"]
            predictions = body[u"predictions"]
            classes = predictions[0][u"classes"]
            for c in classes:
                items = c[u"cat"].strip(" ").split(",")
                prob = c[u"prob"]
                for item in items:
                    items_and_fabrics["items"].append((item, 0.5*prob))

            fabric_res = self.dd.post_predict(self.sname_fabric,data,parameters_input,parameters_mllib,parameters_output)
            body = fabric_res[u"body"]
            predictions = body[u"predictions"]
            classes = predictions[0][u"classes"]
            for c in classes:
                items = c[u"cat"].strip(" ").split(",")
                prob = c[u"prob"]
                for item in items:
                    items_and_fabrics["fabrics"].append((item, prob))
            return items_and_fabrics
        except:
            print("error in deep_detect_LF")
            return items_and_fabrics

    def startup_deep_detect(self):
        """ Startup services for deep detect classification """
        self.dd.set_return_format(self.dd.RETURN_PYTHON)
        for model in self.deep_detect_models:
            m = {"repository": model["path"]}
            parameters_input = {'connector':'image','width':self.width,'height':self.height}
            parameters_mllib = {'nclasses':self.nclasses_clothing}
            parameters_output = {}
            self.dd.put_service(model["name"],model,model["description"],self.mllib,
                                parameters_input,parameters_mllib,parameters_output)

    def deepomatic_lookup(self, link):
        """ Deepomatic API lookup """
        item_candidates = []
        try:
            client = Client(529372386976, self.conf["deepomatic_api_key"])
            task = client.helper.get("/detect/fashion/?url=" + link)
            taskid = task[u"task_id"]
            i = 0
            while i < 10:
                sleep(0.1) #100ms
                res = client.helper.get("/tasks/" + str(taskid) + "/")
                task = res[u"task"]
                status = task[u"status"]
                if status == u"success" or status == "success":
                    data = task[u"data"]
                    boxes = data[u"boxes"]
                    for item in boxes.keys():
                        info = boxes[item]
                        probability = 0.0
                        for inf in info:
                            probability = probability + inf[u"proba"]
                        item_candidates.append((item.encode("utf-8"), probability))
                    i = 10
                else:
                    i += 1
            return item_candidates
        except:
            print("error in deepomaticLF")
            return item_candidates

    def clarifai_lookup(self, link):
        """ Clarifai API lookup"""
        item_candidates = []
        try:
            app = ClarifaiApp(api_key=self.conf["clarifai_api_key"])
            model = app.models.get('apparel')
            image = ClImage(url=link)
            res = model.predict([image])
            outputs = res[u"outputs"]
            for output in outputs:
                data = output[u"data"]
                concepts = data[u"concepts"]
                for concept in concepts:
                    concept_parts = concept[u"name"].encode("utf-8").split(" ")
                    val = concept[u"value"]
                    for part in concept_parts:
                        item_candidates.append((part, val))

            return item_candidates
        except:
            print("error in clarifai LF")
            return item_candidates

    def find_closest_semantic_hierarchy(self, caption, comments, tags, hashtags, topic, id, num):
        """ Finds num semantically closest candidates for a given topic with multiple words per topic"""
        topic = map(lambda x: x.decode('utf-8','ignore').encode("utf-8"), topic)
        freq_scores = {}
        for x in topic:
            parts = x.split(",")
            label = parts[0]
            freq_scores[label] = 0.0
        for token in caption:
            for x in topic:
                parts = x.split(",")
                label = parts[0]
                words = parts[1].split(" ")
                acc_sim = 0
                scores = []
                for word in words:
                    token2 = word.lower()
                    token2Lemma = self.wordnet_lemmatizer.lemmatize(token2)
                    similarity = self.token_similarity(token, token2, token2Lemma, self.CAPTION_FACTOR, self.tfidf[id])
                    scores.append(similarity)
                acc_sim = acc_sim + max(scores)
                freq_scores[label] = freq_scores[label] + acc_sim
        for token in comments:
            for x in topic:
                parts = x.split(",")
                label = parts[0]
                words = parts[1].split(" ")
                acc_sim = 0
                scores = []
                for word in words:
                    token2 = word.lower()
                    token2Lemma = self.wordnet_lemmatizer.lemmatize(token2)
                    similarity = self.token_similarity(token, token2, token2Lemma, self.COMMENTS_FACTOR, self.tfidf[id])
                    scores.append(similarity)
                acc_sim = acc_sim + max(scores)
                freq_scores[label] = freq_scores[label] + acc_sim
        for token in hashtags:
            for x in topic:
                parts = x.split(",")
                label = parts[0]
                words = parts[1].split(" ")
                acc_sim = 0
                scores = []
                for word in words:
                    token2 = word.lower()
                    token2Lemma = self.wordnet_lemmatizer.lemmatize(token2)
                    similarity = self.token_similarity(token, token2, token2Lemma, self.HASHTAG_FACTOR, self.tfidf[id])
                    scores.append(similarity)
                acc_sim = acc_sim + max(scores)
                freq_scores[label] = freq_scores[label] + acc_sim
        for token in tags:
            for x in topic:
                parts = x.split(",")
                label = parts[0]
                words = parts[1].split(" ")
                acc_sim = 0
                scores = []
                for word in words:
                    token2 = word.lower()
                    token2Lemma = self.wordnet_lemmatizer.lemmatize(token2)
                    similarity = self.token_similarity(token, token2, token2Lemma, self.USERTAG_FACTOR, self.tfidf[id])
                    scores.append(similarity)
                    acc_sim = acc_sim + similarity
                acc_sim = acc_sim + max(scores)
                freq_scores[label] = freq_scores[label] + acc_sim
        top = sorted([(k, v) for k, v in freq_scores.iteritems()], reverse=True, key=lambda x: x[1])[:num]
        return top

    def find_closest_syntactic_hierarchy(self, caption, comments, tags, hashtags,topic,id,num):
        """ Finds num syntactically closest candidates for a given topic, with multiple words per topic"""
        topic = map(lambda x: x.decode('utf-8','ignore').encode("utf-8"), topic)
        freq_scores = {}
        for x in topic:
            parts = x.split(",")
            label = parts[0]
            freq_scores[label] = 0.0
        for token in caption:
            for x in topic:
                parts = x.split(",")
                label = parts[0]
                words = parts[1].split(" ")
                acc_sim = 0
                scores = []
                for word in words:
                    token2 = word.lower()
                    token2Lemma = self.wordnet_lemmatizer.lemmatize(token2)
                    similarity = self.token_similarity_syntactic_only(token, token2, token2Lemma, self.CAPTION_FACTOR, self.tfidf[id])
                    scores.append(similarity)
                acc_sim = acc_sim + max(scores)
                freq_scores[label] = freq_scores[label] + acc_sim
        for token in comments:
            for x in topic:
                parts = x.split(",")
                label = parts[0]
                words = parts[1].split(" ")
                acc_sim = 0
                scores = []
                for word in words:
                    token2 = word.lower()
                    token2Lemma = self.wordnet_lemmatizer.lemmatize(token2)
                    similarity = self.token_similarity_syntactic_only(token, token2, token2Lemma, self.COMMENTS_FACTOR, self.tfidf[id])
                    scores.append(similarity)
                acc_sim = acc_sim + max(scores)
                freq_scores[label] = freq_scores[label] + acc_sim
        for token in hashtags:
            for x in topic:
                parts = x.split(",")
                label = parts[0]
                words = parts[1].split(" ")
                acc_sim = 0
                scores = []
                for word in words:
                    token2 = word.lower()
                    token2Lemma = self.wordnet_lemmatizer.lemmatize(token2)
                    similarity = self.token_similarity_syntactic_only(token, token2, token2Lemma, self.HASHTAG_FACTOR, self.tfidf[id])
                    scores.append(similarity)
                acc_sim = acc_sim + max(scores)
                freq_scores[label] = freq_scores[label] + acc_sim
        for token in tags:
            for x in topic:
                parts = x.split(",")
                label = parts[0]
                words = parts[1].split(" ")
                acc_sim = 0
                scores = []
                for word in words:
                    token2 = word.lower()
                    token2Lemma = self.wordnet_lemmatizer.lemmatize(token2)
                    similarity = self.token_similarity_syntactic_only(token, token2, token2Lemma, self.USERTAG_FACTOR, self.tfidf[id])
                    scores.append(similarity)
                    acc_sim = acc_sim + similarity
                acc_sim = acc_sim + max(scores)
                freq_scores[label] = freq_scores[label] + acc_sim
        top = sorted([(k, v) for k, v in freq_scores.iteritems()], reverse=True, key=lambda x: x[1])[:num]
        return top
