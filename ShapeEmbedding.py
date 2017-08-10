import re, string, pickle, numpy, pandas, mojimoji, random, os, jieba, sys
from pyknp import Jumanpp
from keras import optimizers
from keras.models import Model
from keras.layers import Embedding, Input, AveragePooling1D, MaxPooling1D, Conv1D, concatenate, TimeDistributed, \
    Bidirectional, LSTM, Dense, Flatten, GRU
from keras.legacy.layers import Highway
from keras.callbacks import ReduceLROnPlateau, EarlyStopping, CSVLogger, ModelCheckpoint
from keras.utils.np_utils import to_categorical
from keras.preprocessing.sequence import pad_sequences
from attention import AttentionWithContext
from getShapeCode import get_all_word_bukken, get_all_character
from janome.tokenizer import Tokenizer as JanomeTokenizer
from keras import backend as K

# MAX_SENTENCE_LENGTH = 739  # large number as 739 makes cudnn die
MAX_SENTENCE_LENGTH = 500
MAX_WORD_LENGTH = 4
COMP_WIDTH = 3
CHAR_EMB_DIM = 15
VALIDATION_SPLIT = 0.1
TEST_SPLIT = 0.1
BATCH_SIZE = 100
WORD_DIM = 600
MAX_RUN = 1


def _make_kana_convertor():
    # by http://d.hatena.ne.jp/mohayonao/20091129/1259505966
    """ひらがな⇔カタカナ変換器を作る"""
    kata = {
        'ア': 'あ', 'イ': 'い', 'ウ': 'う', 'エ': 'え', 'オ': 'お',
        'カ': 'か', 'キ': 'き', 'ク': 'く', 'ケ': 'け', 'コ': 'こ',
        'サ': 'さ', 'シ': 'し', 'ス': 'す', 'セ': 'せ', 'ソ': 'そ',
        'タ': 'た', 'チ': 'ち', 'ツ': 'つ', 'テ': 'て', 'ト': 'と',
        'ナ': 'な', 'ニ': 'に', 'ヌ': 'ぬ', 'ネ': 'ね', 'ノ': 'の',
        'ハ': 'は', 'ヒ': 'ひ', 'フ': 'ふ', 'ヘ': 'へ', 'ホ': 'ほ',
        'マ': 'ま', 'ミ': 'み', 'ム': 'む', 'メ': 'め', 'モ': 'も',
        'ヤ': 'や', 'ユ': 'ゆ', 'ヨ': 'よ', 'ラ': 'ら', 'リ': 'り',
        'ル': 'る', 'レ': 'れ', 'ロ': 'ろ', 'ワ': 'わ', 'ヲ': 'を',
        'ン': 'ん',

        'ガ': 'が', 'ギ': 'ぎ', 'グ': 'ぐ', 'ゲ': 'げ', 'ゴ': 'ご',
        'ザ': 'ざ', 'ジ': 'じ', 'ズ': 'ず', 'ゼ': 'ぜ', 'ゾ': 'ぞ',
        'ダ': 'だ', 'ヂ': 'ぢ', 'ヅ': 'づ', 'デ': 'で', 'ド': 'ど',
        'バ': 'ば', 'ビ': 'び', 'ブ': 'ぶ', 'ベ': 'べ', 'ボ': 'ぼ',
        'パ': 'ぱ', 'ピ': 'ぴ', 'プ': 'ぷ', 'ペ': 'ぺ', 'ポ': 'ぽ',

        'ァ': 'ぁ', 'ィ': 'ぃ', 'ゥ': 'ぅ', 'ェ': 'ぇ', 'ォ': 'ぉ',
        'ャ': 'ゃ', 'ュ': 'ゅ', 'ョ': 'ょ',
        'ッ': 'っ', 'ヰ': 'ゐ', 'ヱ': 'ゑ',
    }

    # ひらがな → カタカナ のディクショナリをつくる
    hira = dict([(v, k) for k, v in kata.items()])

    re_hira2kata = re.compile("|".join(map(re.escape, hira)))
    re_kata2hira = re.compile("|".join(map(re.escape, kata)))

    def _hiragana2katakana(text):
        return re_hira2kata.sub(lambda x: hira[x.group(0)], text)

    def _katakana2hiragana(text):
        return re_kata2hira.sub(lambda x: kata[x.group(0)], text)

    return (_hiragana2katakana, _katakana2hiragana, hira.keys())


def load_shape_data(datafile="usc-shape_bukken_data.pickle"):
    with open(datafile) as f:
        data = pickle.load(f)
    return data["words"], data["bukkens"], data["word_bukken"]


def get_vocab(opts=None):
    # convert kata to hira
    char_emb_dim = CHAR_EMB_DIM
    use_component = True  # True for component level False for chara level

    _, _, hirakana_list = _make_kana_convertor()
    addition_translate = str.maketrans("ッャュョヮヵヶ?？⁇⁈⁉﹗!‼！″＂“”『』「」‘’´｀:;。、・"
                                       "＼([｛)]｝〔〕【〘〖】〙〗｟｠«»ー－—–‐゠〜～〳〵￥",
                                       "っゃゅょゎゕゖ?????!!!!\"\"\"\"\"\"'''''':;.,･"
                                       "\\((()))()((()))()《》-----=~~/\\$")

    hira_punc_number_latin = "".join(hirakana_list) + string.punctuation + \
                             'ヴゎゕゖㇰㇱㇲㇳㇴㇵㇶㇷㇸㇹㇷ゚ㇺㇻㇼㇽㇾㇿ々〻' \
                             '〟ゝゞ〈《〉》〝…‥･〴' \
                             '1234567890' \
                             'abcdefghijklmnopqrstuvwxyz ' \
                             '○●☆★■♪ヾω*≧∇≦※→←↑↓'
    # note: the space and punctuations in Jp sometimes show emotion

    vocab_chara, vocab_bukken, chara_bukken = get_all_word_bukken()
    hira_punc_number_latin_number = len(hira_punc_number_latin) + 2
    print("totally {n} kana, punctuation and latin char".format(n=str(hira_punc_number_latin_number)))
    vocab = ["</padblank>", "</s>"] + list(hira_punc_number_latin) + vocab_bukken
    real_vocab_number = len(vocab)  # the part of the vocab that is really used. only basic components
    vocab_chara_strip = [chara for chara in vocab_chara if chara not in vocab_bukken]  # delete 独体字
    print("totally {n} puctuation, kana, latin, and chara components".format(n=str(real_vocab_number)))
    full_vocab = vocab + vocab_chara_strip  # add unk at the head, and complex charas for text encoding at the tail
    chara_bukken_revised = {}
    for i_word, i_bukken in chara_bukken.items():  # update the index
        if vocab_chara[i_word] not in vocab_bukken:  # delete 独体字
            chara_bukken_revised[full_vocab.index(vocab_chara[i_word])] = \
                [k + hira_punc_number_latin_number for k in i_bukken]
    del vocab_chara
    del chara_bukken

    return full_vocab, real_vocab_number, chara_bukken_revised, addition_translate, hira_punc_number_latin


def text_to_char_index(full_vocab, real_vocab_number, chara_bukken_revised, sentence_text, addition_translate,
                       mode="padding", comp_width=COMP_WIDTH, preprocessed_char_number=0,
                       skip_unknown=False):
    # mode:
    # average: will repeat the original index to #comp_width for the process of the embedding layer
    # padding: will pad the original index to #comp_width with zero for the process of the embedding layer
    # char_emb_dim  char embedding size
    # comp_width  #components used

    if preprocessed_char_number == 0:
        preprocessed_char_number = len(full_vocab)

    # convert digital number and latin to hangaku
    text = mojimoji.zen_to_han(sentence_text, kana=False)
    # convert kana to zengaku
    text = mojimoji.han_to_zen(text, digit=False, ascii=False)
    # convert kata to hira
    _, katakana2hiragana, _ = _make_kana_convertor()
    text = katakana2hiragana(text)
    text = text.translate(addition_translate)
    # finally, lowercase
    text = text.lower()
    # expanding every character with 3 components
    ch2id = {}
    for i, w in enumerate(full_vocab):
        ch2id[w] = i
    int_text = []
    # print(text)
    if mode == "average":
        for c in text:
            try:
                i = ch2id[c]
            except KeyError:
                if skip_unknown:
                    continue  # skip unknown words
                else:
                    i = 1  # assign to unknown words
            if i > real_vocab_number:
                comps = chara_bukken_revised[i]
                if len(comps) >= comp_width:
                    int_text += comps[:comp_width]
                elif len(comps) == 1:
                    int_text += [i] * comp_width
                else:
                    int_text += comps + [0] * (comp_width - len(comps))
            else:
                int_text += [i] * comp_width
    elif mode == "padding":
        for c in text:
            # print(c)
            try:
                i = ch2id[c]
            except KeyError:
                print("Unknown Character: ", c)
                if skip_unknown:
                    continue  # skip unknown words
                else:
                    i = 1  # assign to unknown words
            # print(i)
            if real_vocab_number < i < preprocessed_char_number:
                comps = chara_bukken_revised[i]
                # print(comps)
                if len(comps) >= comp_width:
                    int_text += comps[:comp_width]
                else:
                    int_text += comps + [0] * (comp_width - len(comps))
            else:
                int_text += [i] + [0] * (comp_width - 1)
    return int_text


def build_word_feature_shape(vocab_size=5, char_emb_dim=CHAR_EMB_DIM, comp_width=COMP_WIDTH,
                             mode="padding", cnn_encoder=True,
                             highway="linear", nohighway=None, shape_filter=True, char_filter=True):
    # build the feature computed by cnn for each word in the sentence. used to input to the next rnn.
    # expected input: every #comp_width int express a character.
    # mode:
    # "average": average pool the every #comp_with input embedding, output average of the indexed embeddings of a character
    # "padding": convoluate every #comp_width embedding

    # real vocab_size for ucs is 2481, including paddingblank, unkown, puncutations, kanas
    assert shape_filter or char_filter
    init_width = 0.5 / char_emb_dim
    init_weight = numpy.random.uniform(low=-init_width, high=init_width, size=(vocab_size, char_emb_dim))
    init_weight[0] = 0  # maybe the padding should not be zero
    # print(init_weight)
    # first layer embeds
    #  every components
    word_input = Input(shape=(COMP_WIDTH * MAX_WORD_LENGTH,))
    char_embedding = \
        Embedding(input_dim=vocab_size, output_dim=char_emb_dim, weights=[init_weight], trainable=True)(word_input)
    # print("char_embedding:", char_embedding._keras_shape)
    if cnn_encoder:
        if mode == "padding":
            # print(char_embedding._keras_shape)
            # print(comp_width)
            if shape_filter and char_filter:
                filter_sizes = [50, 100, 150]
            else:
                filter_sizes = [100, 200, 300]
            if shape_filter:
                feature_s1 = Conv1D(filters=filter_sizes[0], kernel_size=1, activation='relu')(
                    char_embedding)
                feature_s1 = MaxPooling1D(pool_size=MAX_WORD_LENGTH * COMP_WIDTH)(feature_s1)
                feature_s2 = Conv1D(filters=filter_sizes[1], kernel_size=2, activation='relu')(
                    char_embedding)
                feature_s2 = MaxPooling1D(pool_size=MAX_WORD_LENGTH * COMP_WIDTH - 1)(feature_s2)
                feature_s3 = Conv1D(filters=filter_sizes[2], kernel_size=3, activation='relu')(
                    char_embedding)
                feature_s3 = MaxPooling1D(pool_size=MAX_WORD_LENGTH * COMP_WIDTH - 2)(feature_s3)
            if char_filter:
                feature1 = Conv1D(filters=filter_sizes[0], kernel_size=1 * comp_width, strides=comp_width,
                                  activation='relu')(
                    char_embedding)
                feature1 = MaxPooling1D(pool_size=MAX_WORD_LENGTH - 1 + 1)(feature1)
                feature2 = Conv1D(filters=filter_sizes[1], kernel_size=2 * comp_width, strides=comp_width,
                                  activation='relu')(
                    char_embedding)
                feature2 = MaxPooling1D(pool_size=MAX_WORD_LENGTH - 2 + 1)(feature2)
                feature3 = Conv1D(filters=filter_sizes[2], kernel_size=3 * comp_width, strides=comp_width,
                                  activation='relu')(
                    char_embedding)
                feature3 = MaxPooling1D(pool_size=MAX_WORD_LENGTH - 3 + 1)(feature3)
            if shape_filter and char_filter:
                feature = concatenate([feature_s1, feature_s2, feature_s3, feature1, feature2, feature3])
            elif shape_filter and not char_filter:
                feature = concatenate([feature_s1, feature_s2, feature_s3])
            elif char_filter and not shape_filter:
                feature = concatenate([feature1, feature2, feature3])
            else:
                feature = None
        feature = Flatten()(feature)
        # print(feature._keras_shape)
        if highway:
            if isinstance(highway, str):
                feature = Highway(activation=highway)(feature)
            else:
                feature = Highway(activation='relu')(feature)
        else:
            if nohighway:
                feature = Dense(activation=nohighway)(feature)
            else:
                pass
    else:
        feature = Flatten()(char_embedding)
    word_feature_encoder = Model(word_input, feature)
    return word_feature_encoder


def build_word_feature_char(vocab_size=5, char_emb_dim=CHAR_EMB_DIM,
                            mode="padding", cnn_encoder=True, highway=True):
    # build the feature computed by cnn for each word in the sentence. used to input to the next rnn.
    # expected input: every #comp_width int express a character.
    # mode:
    # "average": average pool the every #comp_with input embedding, output average of the indexed embeddings of a character
    # "padding": convoluate every #comp_width embedding

    # real vocab_size for ucs is 2481, including paddingblank, unkown, puncutations, kanas
    init_width = 0.5 / char_emb_dim
    init_weight = numpy.random.uniform(low=-init_width, high=init_width, size=(vocab_size, char_emb_dim))
    init_weight[0] = 0  # maybe the padding should not be zero
    # print(init_weight)
    # first layer embeds
    #  every components
    word_input = Input(shape=(MAX_WORD_LENGTH,))
    char_embedding = \
        Embedding(input_dim=vocab_size, output_dim=char_emb_dim, weights=[init_weight], trainable=True)(word_input)
    # print("char_embedding:", char_embedding._keras_shape)
    if cnn_encoder:
        if mode == "padding":
            # print(char_embedding._keras_shape)
            # conv, filter with [1, 2, 3]*#comp_width, feature maps 50 100 150
            feature1 = Conv1D(filters=100, kernel_size=1, activation='relu')(
                char_embedding)
            feature1 = MaxPooling1D(pool_size=MAX_WORD_LENGTH - 1 + 1)(feature1)
            feature2 = Conv1D(filters=200, kernel_size=2, activation='relu')(
                char_embedding)
            feature2 = MaxPooling1D(pool_size=MAX_WORD_LENGTH - 2 + 1)(feature2)
            feature3 = Conv1D(filters=300, kernel_size=3, activation='relu')(
                char_embedding)
            feature3 = MaxPooling1D(pool_size=MAX_WORD_LENGTH - 3 + 1)(feature3)
            feature = concatenate([feature1, feature2, feature3])
        feature = Flatten()(feature)
        # print(feature._keras_shape)
        if highway:
            feature = Highway(activation="relu")(feature)
    else:
        feature = Flatten()(char_embedding)
    word_feature_encoder = Model(word_input, feature)
    return word_feature_encoder


def build_sentence_rnn(real_vocab_number, word_vocab_size=10, char_vocab_size=10,
                       classes=2, attention=False, dropout=0,
                       word=True, char=False, char_shape=True, model="rnn", cnn_encoder=True,
                       highway=None, nohighway=None, shape_filter=True, char_filter=True):
    # build the rnn of words, use the output of build_word_feature as the feature of each word
    if char_shape:
        word_feature_encoder = build_word_feature_shape(vocab_size=real_vocab_number,
                                                        cnn_encoder=cnn_encoder,
                                                        highway=highway, nohighway=nohighway,
                                                        shape_filter=shape_filter,
                                                        char_filter=char_filter)
        sentence_input = Input(shape=(MAX_SENTENCE_LENGTH, COMP_WIDTH * MAX_WORD_LENGTH), dtype='int32')
        word_feature_sequence = TimeDistributed(word_feature_encoder)(sentence_input)
        # print(word_feature_sequence._keras_shape)
    if word:
        sentence_word_input = Input(shape=(MAX_SENTENCE_LENGTH,), dtype='int32')
        word_embedding_sequence = Embedding(input_dim=word_vocab_size, output_dim=WORD_DIM)(sentence_word_input)
    if char:
        word_feature_encoder = build_word_feature_char(vocab_size=char_vocab_size,
                                                       cnn_encoder=cnn_encoder, highway=highway)
        char_input = Input(shape=(MAX_SENTENCE_LENGTH, MAX_WORD_LENGTH), dtype='int32')
        word_feature_sequence = TimeDistributed(word_feature_encoder)(char_input)
    if char_shape and word and not char:
        word_feature_sequence = concatenate([word_feature_sequence, word_embedding_sequence], axis=2)
    if word and not char_shape and not char:
        word_feature_sequence = word_embedding_sequence
    # print(word_feature_sequence._keras_shape)
    if model == "rnn":
        if attention:
            lstm_rnn = Bidirectional(LSTM(150, dropout=dropout, return_sequences=True))(word_feature_sequence)
            if highway:
                lstm_rnn = TimeDistributed(Highway(activation=highway))(lstm_rnn)
            elif nohighway:
                lstm_rnn = TimeDistributed(Dense(activation=nohighway))(lstm_rnn)
            lstm_rnn = AttentionWithContext()(lstm_rnn)
        else:
            lstm_rnn = Bidirectional(LSTM(150, dropout=dropout, return_sequences=False))(word_feature_sequence)
        x = lstm_rnn
    if classes < 2:
        print("class number cannot less than 2")
        exit(1)
    else:
        preds = Dense(classes, activation='softmax')(x)
    if char_shape and not word and not char:
        sentence_model = Model(sentence_input, preds)
    if word and not char_shape and not char:
        sentence_model = Model(sentence_word_input, preds)
    if word and char_shape and not char:
        sentence_model = Model([sentence_input, sentence_word_input], preds)
    if char and not word and not char_shape:
        sentence_model = Model(char_input, preds)
    sentence_model.summary()
    return sentence_model


def build_hatt(word_vocab_size, classes):
    MAX_SENT_LENGTH = 100
    MAX_SENTS = 5
    sentence_input = Input(shape=(MAX_SENT_LENGTH,), dtype='int32')
    embedded_sequences = Embedding(input_dim=word_vocab_size, output_dim=WORD_DIM, input_length=MAX_SENT_LENGTH)(
        sentence_input)
    l_lstm = Bidirectional(GRU(150, return_sequences=True))(embedded_sequences)
    l_dense = TimeDistributed(Dense(300))(l_lstm)
    l_att = AttentionWithContext()(l_dense)
    sentEncoder = Model(sentence_input, l_att)
    # print("sentEncoder Shape:", l_att._keras_shape)
    review_input = Input(shape=(MAX_SENTS, MAX_SENT_LENGTH), dtype='int32')
    review_encoder = TimeDistributed(sentEncoder)(review_input)
    # print("RevewEncoder Shape:", review_encoder._keras_shape)
    l_lstm_sent = Bidirectional(GRU(150, return_sequences=True))(review_encoder)
    l_dense_sent = TimeDistributed(Dense(300))(l_lstm_sent)
    l_att_sent = AttentionWithContext()(l_dense_sent)
    preds = Dense(classes, activation='softmax')(l_att_sent)
    model = Model(review_input, preds)
    model.summary()
    return model


def build_fasttext(word_vocab_size, classes):
    sentence_input = Input(shape=(MAX_SENTENCE_LENGTH,), dtype='int32')
    embedded_sequences = embedding_layer = Embedding(input_dim=word_vocab_size, output_dim=WORD_DIM)(sentence_input)
    avraged = AveragePooling1D(pool_size=MAX_SENTENCE_LENGTH)(embedded_sequences)
    avraged = Flatten()(avraged)
    l_dens = Dense(10, activation="linear")(avraged)
    preds = Dense(classes, activation='softmax')(l_dens)
    model = Model(sentence_input, preds)
    model.summary()
    return model


def split_data(data_shape, data_char, data_word, labels):
    # split data into training and validation
    indices = numpy.arange(data_char.shape[0])
    numpy.random.shuffle(indices)
    data_shape = data_shape[indices]
    data_word = data_word[indices]
    data_char = data_char[indices]
    labels = labels[indices]
    # 80% to train, 10% to validation, 10% to test
    nb_validation_test_samples = int((VALIDATION_SPLIT+TEST_SPLIT)* data_char.shape[0])
    nb_test_samples = int((TEST_SPLIT)* data_char.shape[0])

    x1_train = data_shape[:-nb_validation_test_samples]
    x2_train = data_word[:-nb_validation_test_samples]
    x3_train = data_char[:-nb_validation_test_samples]
    y_train = labels[:-nb_validation_test_samples]
    x1_val = data_shape[-nb_validation_test_samples:-nb_test_samples]
    x2_val = data_word[-nb_validation_test_samples:-nb_test_samples]
    x3_val = data_char[-nb_validation_test_samples:-nb_test_samples]
    y_val = labels[-nb_validation_test_samples:-nb_test_samples]
    x1_test = data_shape[-nb_test_samples:]
    x2_test = data_word[-nb_test_samples:]
    x3_test = data_char[-nb_test_samples:]
    y_test = labels[-nb_test_samples:]

    return x1_train, x2_train, x3_train, y_train, x1_val, x2_val, x3_val, y_val, \
           x1_test, x2_test, x3_test, y_test


def prepare_ChnSenti_classification(filename="ChnSentiCorp_htl_ba_6000/", dev_mode=False, skip_unk=False):
    # get vocab
    full_vocab, real_vocab_number, chara_bukken_revised, addtional_translate, _ = get_vocab()

    TEXT_DATA_DIR = filename
    texts = []  # list of text samples
    labels_index = {}  # dictionary mapping label name to numeric id
    labels = []  # list of label ids
    # maxlen = 0    # used the max length of sentence in the data. but large number makes cudnn die
    for name in sorted(os.listdir(TEXT_DATA_DIR)):
        path = os.path.join(TEXT_DATA_DIR, name)
        if os.path.isdir(path):
            label_id = len(labels_index)
            labels_index[name] = label_id
            for fname in sorted(os.listdir(path)):
                fpath = os.path.join(path, fname)
                if sys.version_info < (3,):
                    f = open(fpath)
                else:
                    f = open(fpath, encoding='gbk')
                try:
                    t = f.read()
                except UnicodeDecodeError:
                    continue
                t = t.translate(str.maketrans("", "", "\n"))
                t_list = list(jieba.cut(t, cut_all=False))
                # if len(t_list) > maxlen:
                #     maxlen = len(t_list)
                if len(t_list) > MAX_SENTENCE_LENGTH:
                    t_list = t_list[:MAX_SENTENCE_LENGTH]
                texts.append(t_list)
                f.close()
                labels.append(label_id)

    print('Found %s texts.' % len(texts))

    data_size = len(texts)
    preprocessed_char_number = len(full_vocab)

    # global MAX_SENTENCE_LENGTH
    # MAX_SENTENCE_LENGTH = maxlen

    # change the sentence into matrix of word sequence
    data_char = numpy.zeros((data_size, MAX_SENTENCE_LENGTH, COMP_WIDTH * MAX_WORD_LENGTH),
                            dtype=numpy.int32)  # data_shape
    data_word = numpy.zeros((data_size, MAX_SENTENCE_LENGTH), dtype=numpy.int32)
    data_gram = numpy.zeros((data_size, MAX_SENTENCE_LENGTH, MAX_WORD_LENGTH), dtype=numpy.int32)  # data_char
    print("Data shape: {shape}".format(shape=str(data_char.shape)))

    word_vocab = ["</s>"]
    gram_vocab = ["</s>"] + get_all_character()

    num_words = 0
    num_chars = 0
    num_ideographs = 0

    for i, text in enumerate(texts):
        for j, word in enumerate(text):
            # word level
            num_words += 1
            if word not in word_vocab:
                word_vocab.append(word)
                word_index = len(word_vocab) - 1
            else:
                word_index = word_vocab.index(word)
            data_word[i, j] = word_index
            # single char gram level
            for l, char_g in enumerate(word):
                num_chars += 1
                if char_g not in gram_vocab:
                    gram_vocab.append(char_g)
                    char_g_index = len(gram_vocab) - 1
                else:
                    char_g_index = gram_vocab.index(char_g)
                if l < MAX_WORD_LENGTH:
                    data_gram[i, j, l] = char_g_index
                if not skip_unk:
                    if char_g not in full_vocab:
                        full_vocab.append(char_g)
                if real_vocab_number < char_g_index < preprocessed_char_number:
                    num_ideographs += 1
            # char shape level
            char_index = text_to_char_index(full_vocab=full_vocab, real_vocab_number=real_vocab_number,
                                            chara_bukken_revised=chara_bukken_revised,
                                            addition_translate=addtional_translate,
                                            sentence_text=word, preprocessed_char_number=preprocessed_char_number,
                                            skip_unknown=skip_unk)
            if len(char_index) < COMP_WIDTH * MAX_WORD_LENGTH:
                char_index = char_index + [0] * (COMP_WIDTH * MAX_WORD_LENGTH - len(char_index))  # Padding
            elif len(char_index) > COMP_WIDTH * MAX_WORD_LENGTH:
                char_index = char_index[:COMP_WIDTH * MAX_WORD_LENGTH]
            for k, comp in enumerate(char_index):
                data_char[i, j, k] = comp

    print("# words: ", num_words)
    print("# chars: ", num_chars)
    print("# ideographas: ", num_ideographs)
    # convert labels to one-hot vectors
    labels = to_categorical(numpy.asarray(labels))
    print('Label Shape:', labels.shape)

    x1_train, x2_train, x3_train, y_train, x1_val, x2_val, x3_val, y_val, \
    x1_test, x2_test, x3_test, y_test = split_data(data_shape=data_char,
                                                   data_char=data_gram, data_word=data_word,
                                                   labels=labels)

    print('Number of different reviews for training and test')
    print(y_train.sum(axis=0))
    print(y_val.sum(axis=0))

    return full_vocab, real_vocab_number, chara_bukken_revised, word_vocab, gram_vocab, \
           x1_train, x2_train, x3_train, y_train, x1_val, x2_val, x3_val, y_val, \
           x1_test, x2_test, x3_test, y_test


def do_ChnSenti_classification(filename, dev_mode=False, attention=False, cnn_encoder=True,
                               char_shape_only=True, char_only=True, word_only=True,
                               hatt=True, fasttext=True, skip_unk=False,
                               highway=None, nohighway=None, shape_filter=True, char_filter=True):
    picklename = filename[:-1] + ".pickle"
    if not skip_unk:
        if os.path.isfile(picklename):
            f = open(picklename, "rb")
            (full_vocab, real_vocab_number, chara_bukken_revised, word_vocab, char_vocab,
             x1_train, x2_train, x3_train, y_train, x1_val, x2_val, x3_val, y_val,
             x1_test, x2_test, x3_test, y_test) \
                = pickle.load(f)
            f.close()
        else:
            (full_vocab, real_vocab_number, chara_bukken_revised, word_vocab, char_vocab,
             x1_train, x2_train, x3_train, y_train, x1_val, x2_val, x3_val, y_val,
             x1_test, x2_test, x3_test, y_test) \
                = prepare_ChnSenti_classification(filename=filename, dev_mode=dev_mode, skip_unk=skip_unk)
            with open(picklename, "wb") as f:
                pickle.dump((full_vocab, real_vocab_number, chara_bukken_revised, word_vocab, char_vocab,
                             x1_train, x2_train, x3_train, y_train, x1_val, x2_val, x3_val, y_val,
                             x1_test, x2_test, x3_test, y_test), f)
    else:
        (full_vocab, real_vocab_number, chara_bukken_revised, word_vocab, char_vocab,
         x1_train, x2_train, x3_train, y_train, x1_val, x2_val, x3_val, y_val,
         x1_test, x2_test, x3_test, y_test) \
            = prepare_ChnSenti_classification(filename=filename, dev_mode=dev_mode, skip_unk=skip_unk)

    word_vocab_size = len(word_vocab)
    char_vocab_size = len(char_vocab)

    print("word vocab size", word_vocab_size)
    print("char vocab size", char_vocab_size)

    num_class = 2
    reducelr = ReduceLROnPlateau(monitor='val_loss', factor=0.1, patience=5)
    stopper = EarlyStopping(monitor='val_loss', patience=10)

    if char_shape_only:
        print("MODEL: 1 Char Shape Only. No highway, No Attention")
        for run_num in range(MAX_RUN):
            print("RUN: ", run_num)
            checkpoint_acc = ModelCheckpoint(filepath="checkpoints/MODELCH1_run"+str(run_num)+"_bestacc.hdf5",
                                             monitor="val_acc", verbose=1, save_best_only=True,
                                             mode="max")
            checkpoint_loss = ModelCheckpoint(filepath="checkpoints/MODELCH1_run"+str(run_num)+"_bestloss.hdf5",
                                             monitor="val_loss", verbose=1, save_best_only=True,
                                             mode="min")
            model2 = build_sentence_rnn(real_vocab_number=real_vocab_number, classes=num_class,
                                        char_shape=True, word=False, char=False,
                                        cnn_encoder=cnn_encoder, highway=None, nohighway=None,
                                        attention=False,
                                        shape_filter=shape_filter, char_filter=char_filter)
            model2.compile(loss='categorical_crossentropy',
                           optimizer='rmsprop',
                           metrics=['acc'], )
            print("START FITTING")
            model2.fit(x1_train, y_train, validation_data=(x1_val, y_val),
                       epochs=100, batch_size=BATCH_SIZE,
                       callbacks=[reducelr, stopper, checkpoint_acc, checkpoint_loss])

        print("MODEL: 2 Char Shape Only. No highway, Attention")
        for run_num in range(MAX_RUN):
            print("RUN: ", run_num)
            checkpoint_acc = ModelCheckpoint(filepath="checkpoints/MODELCH2_run"+str(run_num)+"_bestacc.hdf5",
                                             monitor="val_acc", verbose=1, save_best_only=True,
                                             mode="max")
            checkpoint_loss = ModelCheckpoint(filepath="checkpoints/MODELCH2_run"+str(run_num)+"_bestloss.hdf5",
                                             monitor="val_loss", verbose=1, save_best_only=True,
                                             mode="min")
            model2 = build_sentence_rnn(real_vocab_number=real_vocab_number, classes=num_class,
                                        char_shape=True, word=False, char=False,
                                        cnn_encoder=cnn_encoder, highway=None, nohighway=None,
                                        attention=True,
                                        shape_filter=shape_filter, char_filter=char_filter)
            model2.compile(loss='categorical_crossentropy',
                           optimizer='rmsprop',
                           metrics=['acc'], )
            print("START FITTING")
            model2.fit(x1_train, y_train, validation_data=(x1_val, y_val),
                       epochs=100, batch_size=BATCH_SIZE,
                       callbacks=[reducelr, stopper, checkpoint_acc, checkpoint_loss])

        print("MODEL: 3 Char Shape Only. No highway but Linear Dense, No Attention")
        for run_num in range(MAX_RUN):
            print("RUN: ", run_num)
            checkpoint_acc = ModelCheckpoint(filepath="checkpoints/MODELCH3_run"+str(run_num)+"_bestacc.hdf5",
                                             monitor="val_acc", verbose=1, save_best_only=True,
                                             mode="max")
            checkpoint_loss = ModelCheckpoint(filepath="checkpoints/MODELCH3_run"+str(run_num)+"_bestloss.hdf5",
                                             monitor="val_loss", verbose=1, save_best_only=True,
                                             mode="min")
            model2 = build_sentence_rnn(real_vocab_number=real_vocab_number, classes=num_class,
                                        char_shape=True, word=False, char=False,
                                        cnn_encoder=cnn_encoder, highway=None, nohighway="linear",
                                        attention=False,
                                        shape_filter=shape_filter, char_filter=char_filter)
            model2.compile(loss='categorical_crossentropy',
                           optimizer='rmsprop',
                           metrics=['acc'], )
            print("START FITTING")
            model2.fit(x1_train, y_train, validation_data=(x1_val, y_val),
                       epochs=100, batch_size=BATCH_SIZE,
                       callbacks=[reducelr, stopper, checkpoint_acc, checkpoint_loss])

        print("MODEL: 4 Char Shape Only. No highway but ReLU Dense, No Attention")
        for run_num in range(MAX_RUN):
            print("RUN: ", run_num)
            checkpoint_acc = ModelCheckpoint(filepath="checkpoints/MODELCH4_run"+str(run_num)+"_bestacc.hdf5",
                                             monitor="val_acc", verbose=1, save_best_only=True,
                                             mode="max")
            checkpoint_loss = ModelCheckpoint(filepath="checkpoints/MODELCH4_run"+str(run_num)+"_bestloss.hdf5",
                                             monitor="val_loss", verbose=1, save_best_only=True,
                                             mode="min")
            model2 = build_sentence_rnn(real_vocab_number=real_vocab_number, classes=num_class,
                                        char_shape=True, word=False, char=False,
                                        cnn_encoder=cnn_encoder, highway=None, nohighway="relu",
                                        attention=False,
                                        shape_filter=shape_filter, char_filter=char_filter)
            model2.compile(loss='categorical_crossentropy',
                           optimizer='rmsprop',
                           metrics=['acc'], )
            print("START FITTING")
            model2.fit(x1_train, y_train, validation_data=(x1_val, y_val),
                       epochs=100, batch_size=BATCH_SIZE,
                       callbacks=[reducelr, stopper, checkpoint_acc, checkpoint_loss])

        print("MODEL: 5 Char Shape Only. Linear Highway, No Attention")
        for run_num in range(MAX_RUN):
            print("RUN: ", run_num)
            checkpoint_acc = ModelCheckpoint(filepath="checkpoints/MODELCH5_run"+str(run_num)+"_bestacc.hdf5",
                                             monitor="val_acc", verbose=1, save_best_only=True,
                                             mode="max")
            checkpoint_loss = ModelCheckpoint(filepath="checkpoints/MODELCH5_run"+str(run_num)+"_bestloss.hdf5",
                                             monitor="val_loss", verbose=1, save_best_only=True,
                                             mode="min")
            model2 = build_sentence_rnn(real_vocab_number=real_vocab_number, classes=num_class,
                                        char_shape=True, word=False, char=False,
                                        cnn_encoder=cnn_encoder, highway="linear", nohighway=None,
                                        attention=False,
                                        shape_filter=shape_filter, char_filter=char_filter)
            model2.compile(loss='categorical_crossentropy',
                           optimizer='rmsprop',
                           metrics=['acc'], )
            print("START FITTING")
            model2.fit(x1_train, y_train, validation_data=(x1_val, y_val),
                       epochs=100, batch_size=BATCH_SIZE,
                       callbacks=[reducelr, stopper, checkpoint_acc, checkpoint_loss])

        print("MODEL: 6 Char Shape Only. ReLU Highway, No Attention")
        for run_num in range(MAX_RUN):
            print("RUN: ", run_num)
            checkpoint_acc = ModelCheckpoint(filepath="checkpoints/MODELCH6_run"+str(run_num)+"_bestacc.hdf5",
                                             monitor="val_acc", verbose=1, save_best_only=True,
                                             mode="max")
            checkpoint_loss = ModelCheckpoint(filepath="checkpoints/MODELCH6_run"+str(run_num)+"_bestloss.hdf5",
                                             monitor="val_loss", verbose=1, save_best_only=True,
                                             mode="min")
            model2 = build_sentence_rnn(real_vocab_number=real_vocab_number, classes=num_class,
                                        char_shape=True, word=False, char=False,
                                        cnn_encoder=cnn_encoder, highway="relu", nohighway=None,
                                        attention=False,
                                        shape_filter=shape_filter, char_filter=char_filter)
            model2.compile(loss='categorical_crossentropy',
                           optimizer='rmsprop',
                           metrics=['acc'], )
            print("START FITTING")
            model2.fit(x1_train, y_train, validation_data=(x1_val, y_val),
                       epochs=100, batch_size=BATCH_SIZE,
                       callbacks=[reducelr, stopper, checkpoint_acc, checkpoint_loss])

    if char_only:
        print("MODEL: 7 Char Only No Highway No Attention")
        for run_num in range(MAX_RUN):
            print("RUN: ", run_num)
            checkpoint_acc = ModelCheckpoint(filepath="checkpoints/MODELCH7_run"+str(run_num)+"_bestacc.hdf5",
                                             monitor="val_acc", verbose=1, save_best_only=True,
                                             mode="max")
            checkpoint_loss = ModelCheckpoint(filepath="checkpoints/MODELCH7_run"+str(run_num)+"_bestloss.hdf5",
                                             monitor="val_loss", verbose=1, save_best_only=True,
                                             mode="min")
            model2 = build_sentence_rnn(real_vocab_number=real_vocab_number, char_vocab_size=char_vocab_size,
                                        classes=num_class,
                                        attention=False, word=False, char=True, char_shape=False,
                                        cnn_encoder=cnn_encoder, highway=None,
                                        shape_filter=shape_filter, char_filter=char_filter)
            model2.compile(loss='categorical_crossentropy',
                           optimizer='rmsprop',
                           metrics=['acc'], )
            model2.fit(x3_train, y_train, validation_data=(x3_val, y_val),
                       epochs=100, batch_size=BATCH_SIZE,
                       callbacks=[reducelr, stopper, checkpoint_acc, checkpoint_loss])
        print("MODEL: 8 Char Only No Highway Attention")
        for run_num in range(MAX_RUN):
            print("RUN: ", run_num)
            checkpoint_acc = ModelCheckpoint(filepath="checkpoints/MODELCH8_run"+str(run_num)+"_bestacc.hdf5",
                                             monitor="val_acc", verbose=1, save_best_only=True,
                                             mode="max")
            checkpoint_loss = ModelCheckpoint(filepath="checkpoints/MODELCH8_run"+str(run_num)+"_bestloss.hdf5",
                                             monitor="val_loss", verbose=1, save_best_only=True,
                                             mode="min")
            model2 = build_sentence_rnn(real_vocab_number=real_vocab_number, char_vocab_size=char_vocab_size,
                                        classes=num_class,
                                        attention=True, word=False, char=True, char_shape=False,
                                        cnn_encoder=cnn_encoder, highway=None,
                                        shape_filter=shape_filter, char_filter=char_filter)
            model2.compile(loss='categorical_crossentropy',
                           optimizer='rmsprop',
                           metrics=['acc'], )
            model2.fit(x3_train, y_train, validation_data=(x3_val, y_val),
                       epochs=100, batch_size=BATCH_SIZE,
                       callbacks=[reducelr, stopper, checkpoint_acc, checkpoint_loss])
        print("MODEL: 9 Char Only Linear Dense No Attention")
        for run_num in range(MAX_RUN):
            print("RUN: ", run_num)
            checkpoint_acc = ModelCheckpoint(filepath="checkpoints/MODELCH9_run"+str(run_num)+"_bestacc.hdf5",
                                             monitor="val_acc", verbose=1, save_best_only=True,
                                             mode="max")
            checkpoint_loss = ModelCheckpoint(filepath="checkpoints/MODELCH9_run"+str(run_num)+"_bestloss.hdf5",
                                             monitor="val_loss", verbose=1, save_best_only=True,
                                             mode="min")
            model2 = build_sentence_rnn(real_vocab_number=real_vocab_number, char_vocab_size=char_vocab_size,
                                        classes=num_class,
                                        attention=False, word=False, char=True, char_shape=False,
                                        cnn_encoder=cnn_encoder, highway=None, nohighway="linear",
                                        shape_filter=shape_filter, char_filter=char_filter)
            model2.compile(loss='categorical_crossentropy',
                           optimizer='rmsprop',
                           metrics=['acc'], )
            model2.fit(x3_train, y_train, validation_data=(x3_val, y_val),
                       epochs=100, batch_size=BATCH_SIZE,
                       callbacks=[reducelr, stopper, checkpoint_acc, checkpoint_loss])
        print("MODEL: 10 Char Only ReLU Dense No Attention")
        for run_num in range(MAX_RUN):
            print("RUN: ", run_num)
            checkpoint_acc = ModelCheckpoint(filepath="checkpoints/MODELCH10_run"+str(run_num)+"_bestacc.hdf5",
                                             monitor="val_acc", verbose=1, save_best_only=True,
                                             mode="max")
            checkpoint_loss = ModelCheckpoint(filepath="checkpoints/MODELCH10_run"+str(run_num)+"_bestloss.hdf5",
                                             monitor="val_loss", verbose=1, save_best_only=True,
                                             mode="min")
            model2 = build_sentence_rnn(real_vocab_number=real_vocab_number, char_vocab_size=char_vocab_size,
                                        classes=num_class,
                                        attention=False, word=False, char=True, char_shape=False,
                                        cnn_encoder=cnn_encoder, highway=None, nohighway="relu",
                                        shape_filter=shape_filter, char_filter=char_filter)
            model2.compile(loss='categorical_crossentropy',
                           optimizer='rmsprop',
                           metrics=['acc'], )
            model2.fit(x3_train, y_train, validation_data=(x3_val, y_val),
                       epochs=100, batch_size=BATCH_SIZE,
                       callbacks=[reducelr, stopper, checkpoint_acc, checkpoint_loss])
        print("MODEL: 11 Char Only Linear Highway No Attention")
        for run_num in range(MAX_RUN):
            print("RUN: ", run_num)
            checkpoint_acc = ModelCheckpoint(filepath="checkpoints/MODELCH11_run"+str(run_num)+"_bestacc.hdf5",
                                             monitor="val_acc", verbose=1, save_best_only=True,
                                             mode="max")
            checkpoint_loss = ModelCheckpoint(filepath="checkpoints/MODELCH11_run"+str(run_num)+"_bestloss.hdf5",
                                             monitor="val_loss", verbose=1, save_best_only=True,
                                             mode="min")
            model2 = build_sentence_rnn(real_vocab_number=real_vocab_number, char_vocab_size=char_vocab_size,
                                        classes=num_class,
                                        attention=False, word=False, char=True, char_shape=False,
                                        cnn_encoder=cnn_encoder, highway="linear", nohighway=None,
                                        shape_filter=shape_filter, char_filter=char_filter)
            model2.compile(loss='categorical_crossentropy',
                           optimizer='rmsprop',
                           metrics=['acc'], )
            model2.fit(x3_train, y_train, validation_data=(x3_val, y_val),
                       epochs=100, batch_size=BATCH_SIZE,
                       callbacks=[reducelr, stopper, checkpoint_acc, checkpoint_loss])
        print("MODEL: 12 Char Only ReLU Highway No Attention")
        for run_num in range(MAX_RUN):
            print("RUN: ", run_num)
            checkpoint_acc = ModelCheckpoint(filepath="checkpoints/MODELCH12_run"+str(run_num)+"_bestacc.hdf5",
                                             monitor="val_acc", verbose=1, save_best_only=True,
                                             mode="max")
            checkpoint_loss = ModelCheckpoint(filepath="checkpoints/MODELCH12_run"+str(run_num)+"_bestloss.hdf5",
                                             monitor="val_loss", verbose=1, save_best_only=True,
                                             mode="min")
            model2 = build_sentence_rnn(real_vocab_number=real_vocab_number, char_vocab_size=char_vocab_size,
                                        classes=num_class,
                                        attention=False, word=False, char=True, char_shape=False,
                                        cnn_encoder=cnn_encoder, highway="relu", nohighway=None,
                                        shape_filter=shape_filter, char_filter=char_filter)
            model2.compile(loss='categorical_crossentropy',
                           optimizer='rmsprop',
                           metrics=['acc'], )
            model2.fit(x3_train, y_train, validation_data=(x3_val, y_val),
                       epochs=100, batch_size=BATCH_SIZE,
                       callbacks=[reducelr, stopper, checkpoint_acc, checkpoint_loss])
    if word_only:
        print("MODEL: 13 Word Only No Attention")
        for run_num in range(MAX_RUN):
            print("RUN: ", run_num)
            checkpoint_acc = ModelCheckpoint(filepath="checkpoints/MODELCH13_run"+str(run_num)+"_bestacc.hdf5",
                                             monitor="val_acc", verbose=1, save_best_only=True,
                                             mode="max")
            checkpoint_loss = ModelCheckpoint(filepath="checkpoints/MODELCH13_run"+str(run_num)+"_bestloss.hdf5",
                                             monitor="val_loss", verbose=1, save_best_only=True,
                                             mode="min")
            model4 = build_sentence_rnn(real_vocab_number=real_vocab_number, word_vocab_size=word_vocab_size,
                                        classes=num_class, attention=False, char_shape=False, char=False,
                                        cnn_encoder=cnn_encoder)
            model4.compile(loss='categorical_crossentropy',
                           optimizer='rmsprop',
                           metrics=['acc'])
            model4.fit(x2_train, y_train, validation_data=(x2_val, y_val),
                       epochs=100, batch_size=BATCH_SIZE,
                       callbacks=[reducelr, stopper, checkpoint_acc, checkpoint_loss])

        print("MODEL: 14 Word Only Attention")
        for run_num in range(MAX_RUN):
            print("RUN: ", run_num)
            checkpoint_acc = ModelCheckpoint(filepath="checkpoints/MODELCH14_run"+str(run_num)+"_bestacc.hdf5",
                                             monitor="val_acc", verbose=1, save_best_only=True,
                                             mode="max")
            checkpoint_loss = ModelCheckpoint(filepath="checkpoints/MODELCH14_run"+str(run_num)+"_bestloss.hdf5",
                                             monitor="val_loss", verbose=1, save_best_only=True,
                                             mode="min")
            model4 = build_sentence_rnn(real_vocab_number=real_vocab_number, word_vocab_size=word_vocab_size,
                                        classes=num_class, attention=True, char_shape=False, char=False,
                                        cnn_encoder=cnn_encoder)
            model4.compile(loss='categorical_crossentropy',
                           optimizer='rmsprop',
                           metrics=['acc'])
            model4.fit(x2_train, y_train, validation_data=(x2_val, y_val),
                       epochs=100, batch_size=BATCH_SIZE,
                       callbacks=[reducelr, stopper, checkpoint_loss, checkpoint_acc])
    if hatt:
        print("MODEL: 15 HATT")
        for run_num in range(MAX_RUN):
            print("RUN: ", run_num)
            checkpoint_acc = ModelCheckpoint(filepath="checkpoints/MODELCH15_run"+str(run_num)+"_bestacc.hdf5",
                                             monitor="val_acc", verbose=1, save_best_only=True,
                                             mode="max")
            checkpoint_loss = ModelCheckpoint(filepath="checkpoints/MODELCH15_run"+str(run_num)+"_bestloss.hdf5",
                                             monitor="val_loss", verbose=1, save_best_only=True,
                                             mode="min")
            x_train = numpy.reshape(x2_train, (x2_train.shape[0], 5, 100))
            x_val = numpy.reshape(x2_val, (x2_val.shape[0], 5, 100))
            model = build_hatt(word_vocab_size, 2)
            model.compile(loss="categorical_crossentropy", optimizer="rmsprop", metrics=["acc"], )
            model.fit(x_train, y_train, validation_data=(x_val, y_val),
                      epochs=100, batch_size=BATCH_SIZE,
                      callbacks=[reducelr, stopper, checkpoint_acc, checkpoint_loss])

    if fasttext:
        print("MODEL: 16 fastText")
        for run_num in range(MAX_RUN):
            print("RUN: ", run_num)
            checkpoint_acc = ModelCheckpoint(filepath="checkpoints/MODELCH16_run"+str(run_num)+"_bestacc.hdf5",
                                             monitor="val_acc", verbose=1, save_best_only=True,
                                             mode="max")
            checkpoint_loss = ModelCheckpoint(filepath="checkpoints/MODELCH16_run"+str(run_num)+"_bestloss.hdf5",
                                             monitor="val_loss", verbose=1, save_best_only=True,
                                             mode="min")
            model = build_fasttext(word_vocab_size, 2)
            model.compile(loss="categorical_crossentropy", optimizer="rmsprop", metrics=["acc"], )
            model.fit(x2_train, y_train, validation_data=(x2_val, y_val),
                      epochs=100, batch_size=BATCH_SIZE,
                      callbacks=[reducelr, stopper, checkpoint_acc, checkpoint_loss])


def prepare_rakuten_senti_classification(datasize, skip_unk=False):
    juman = Jumanpp()
    janome_tokenizer = JanomeTokenizer()
    full_vocab, real_vocab_number, chara_bukken_revised, addtional_translate, _ = get_vocab()
    data_limit_per_class = datasize // 2
    data_size = data_limit_per_class * 2
    with open("rakuten/rakuten_review.pickle", "rb") as f:
        positive, negative = pickle.load(f)
    random.shuffle(positive)
    random.shuffle(negative)
    positive = positive[:data_limit_per_class]
    negative = negative[:data_limit_per_class]
    labels = [1] * data_limit_per_class + [0] * data_limit_per_class

    preprocessed_char_number = len(full_vocab)

    data_shape = numpy.zeros((data_size, MAX_SENTENCE_LENGTH, COMP_WIDTH * MAX_WORD_LENGTH), dtype=numpy.int32)
    data_word = numpy.zeros((data_size, MAX_SENTENCE_LENGTH), dtype=numpy.int32)
    data_char = numpy.zeros((data_size, MAX_SENTENCE_LENGTH, MAX_WORD_LENGTH), dtype=numpy.int32)

    print("Data_shape shape: {shape}".format(shape=str(data_shape.shape)))

    word_vocab = ["</s>"]
    char_vocab = ["</s>"] + get_all_character()

    num_words = 0
    num_chars = 0
    num_ideographs = 0

    for i, text in enumerate(positive + negative):
        # 日语分词
        janome = False
        try:
            parse_result = juman.analysis(text)
            parse_tokens = parse_result.mrph_list()
        except ValueError:
            # print(sys.exc_info())
            parse_tokens = janome_tokenizer.tokenize(text)
            janome = True
        except:
            print("Unexpected error:", sys.exc_info()[0])
            raise
        for j, mrph in enumerate(parse_tokens):
            num_words += 1
            if j + 1 > MAX_SENTENCE_LENGTH:
                break
            if janome:
                word = mrph.surface
                word_genkei = mrph.base_form
            else:
                word = mrph.midasi
                word_genkei = mrph.genkei
            # word level
            if word_genkei not in word_vocab:
                word_vocab.append(word_genkei)
                word_index = len(word_vocab) - 1
            else:
                word_index = word_vocab.index(word_genkei)
            data_word[i, j] = word_index
            # single char gram level
            for l, char_g in enumerate(word):
                num_chars += 1
                if char_g not in char_vocab:
                    char_vocab.append(char_g)
                    char_g_index = len(char_vocab) - 1
                else:
                    char_g_index = char_vocab.index(char_g)
                if l < MAX_WORD_LENGTH:
                    data_char[i, j, l] = char_g_index
                if not skip_unk:
                    if char_g not in full_vocab:
                        full_vocab.append(char_g)
                if real_vocab_number < char_g_index < preprocessed_char_number:
                    num_ideographs += 1
            # char shape level
            char_index = text_to_char_index(full_vocab=full_vocab, real_vocab_number=real_vocab_number,
                                            chara_bukken_revised=chara_bukken_revised,
                                            addition_translate=addtional_translate,
                                            preprocessed_char_number=preprocessed_char_number,
                                            sentence_text=word, skip_unknown=skip_unk)
            if len(char_index) < COMP_WIDTH * MAX_WORD_LENGTH:
                char_index = char_index + [0] * (COMP_WIDTH * MAX_WORD_LENGTH - len(char_index))  # Padding
            elif len(char_index) > COMP_WIDTH * MAX_WORD_LENGTH:
                char_index = char_index[:COMP_WIDTH * MAX_WORD_LENGTH]
            for k, comp in enumerate(char_index):
                data_shape[i, j, k] = comp

    print("# words: ", num_words)
    print("# chars: ", num_chars)
    print("# ideographas: ", num_ideographs)
    # convert labels to one-hot vectors
    labels = to_categorical(numpy.asarray(labels))
    print('Label Shape:', labels.shape)

    x1_train, x2_train, x3_train, y_train, x1_val, x2_val, x3_val, y_val, \
    x1_test, x2_test, x3_test, y_test = split_data(data_shape=data_shape, data_word=data_word,
                                                   data_char=data_char, labels=labels)

    print('Number of different reviews for training and test')
    print(y_train.sum(axis=0))
    print(y_val.sum(axis=0))

    return full_vocab, real_vocab_number, chara_bukken_revised, word_vocab, char_vocab, \
           x1_train, x2_train, x3_train, y_train, x1_val, x2_val, x3_val, y_val, \
           x1_test, x2_test, x3_test, y_test


def do_rakuten_senti_classification(datasize, attention=False, cnn_encoder=True,
                                    char_shape_only=True, char_only=True, word_only=True,
                                    hatt=True, fasttext=True,
                                    skip_unk=False, highway=True, shape_filter=True, char_filter=True):
    picklename = "Rakuten" + str(datasize) + ".pickle"
    if not skip_unk:
        if os.path.isfile(picklename):
            f = open(picklename, "rb")
            (full_vocab, real_vocab_number, chara_bukken_revised, word_vocab, char_vocab,
             x1_train, x2_train, x3_train, y_train, x1_val, x2_val, x3_val, y_val,
             x1_test, x2_test, x3_test, y_test) \
                = pickle.load(f)
            f.close()
        else:
            (full_vocab, real_vocab_number, chara_bukken_revised, word_vocab, char_vocab,
             x1_train, x2_train, x3_train, y_train, x1_val, x2_val, x3_val, y_val,
             x1_test, x2_test, x3_test, y_test) \
                = prepare_rakuten_senti_classification(datasize, skip_unk=skip_unk)
            with open("Rakuten" + str(datasize) + ".pickle", "wb") as f:
                pickle.dump((full_vocab, real_vocab_number, chara_bukken_revised, word_vocab, char_vocab,
                             x1_train, x2_train, x3_train, y_train, x1_val, x2_val, x3_val, y_val,
                             x1_test, x2_test, x3_test, y_test), f)
    else:
        (full_vocab, real_vocab_number, chara_bukken_revised, word_vocab, char_vocab,
         x1_train, x2_train, x3_train, y_train, x1_val, x2_val, x3_val, y_val,
         x1_test, x2_test, x3_test, y_test) \
            = prepare_rakuten_senti_classification(datasize, skip_unk=skip_unk)

    word_vocab_size = len(word_vocab)
    char_vocab_size = len(char_vocab)

    print("word vocab size", word_vocab_size)
    print("char vocab size", char_vocab_size)

    num_class = 2
    reducelr = ReduceLROnPlateau(monitor='val_loss', factor=0.1, patience=5)
    stopper = EarlyStopping(monitor='val_loss', patience=10)

    if char_shape_only:
        print("MODEL: 1 Char Shape Only. No highway, No Attention")
        for run_num in range(MAX_RUN):
            print("RUN: ", run_num)
            checkpoint_acc = ModelCheckpoint(filepath="checkpoints/MODELRAKUTEN1_run"+str(run_num)+"_bestacc.hdf5",
                                             monitor="val_acc", verbose=1, save_best_only=True,
                                             mode="max")
            checkpoint_loss = ModelCheckpoint(filepath="checkpoints/MODELRAKUTEN1_run"+str(run_num)+"_bestloss.hdf5",
                                             monitor="val_loss", verbose=1, save_best_only=True,
                                             mode="min")
            model2 = build_sentence_rnn(real_vocab_number=real_vocab_number, classes=num_class,
                                        char_shape=True, word=False, char=False,
                                        cnn_encoder=cnn_encoder, highway=None, nohighway=None,
                                        attention=False,
                                        shape_filter=shape_filter, char_filter=char_filter)
            model2.compile(loss='categorical_crossentropy',
                           optimizer='rmsprop',
                           metrics=['acc'], )
            print("START FITTING")
            model2.fit(x1_train, y_train, validation_data=(x1_val, y_val),
                       epochs=100, batch_size=BATCH_SIZE,
                       callbacks=[reducelr, stopper, checkpoint_acc, checkpoint_loss])

        print("MODEL: 2 Char Shape Only. No highway, Attention")
        for run_num in range(MAX_RUN):
            print("RUN: ", run_num)
            checkpoint_acc = ModelCheckpoint(filepath="checkpoints/MODELRAKUTEN2_run"+str(run_num)+"_bestacc.hdf5",
                                             monitor="val_acc", verbose=1, save_best_only=True,
                                             mode="max")
            checkpoint_loss = ModelCheckpoint(filepath="checkpoints/MODELRAKUTEN2_run"+str(run_num)+"_bestloss.hdf5",
                                             monitor="val_loss", verbose=1, save_best_only=True,
                                             mode="min")
            model2 = build_sentence_rnn(real_vocab_number=real_vocab_number, classes=num_class,
                                        char_shape=True, word=False, char=False,
                                        cnn_encoder=cnn_encoder, highway=None, nohighway=None,
                                        attention=True,
                                        shape_filter=shape_filter, char_filter=char_filter)
            model2.compile(loss='categorical_crossentropy',
                           optimizer='rmsprop',
                           metrics=['acc'], )
            print("START FITTING")
            model2.fit(x1_train, y_train, validation_data=(x1_val, y_val),
                       epochs=100, batch_size=BATCH_SIZE,
                       callbacks=[reducelr, stopper, checkpoint_acc, checkpoint_loss])

        print("MODEL: 3 Char Shape Only. No highway but Linear Dense, No Attention")
        for run_num in range(MAX_RUN):
            print("RUN: ", run_num)
            checkpoint_acc = ModelCheckpoint(filepath="checkpoints/MODELRAKUTEN3_run"+str(run_num)+"_bestacc.hdf5",
                                             monitor="val_acc", verbose=1, save_best_only=True,
                                             mode="max")
            checkpoint_loss = ModelCheckpoint(filepath="checkpoints/MODELRAKUTEN3_run"+str(run_num)+"_bestloss.hdf5",
                                             monitor="val_loss", verbose=1, save_best_only=True,
                                             mode="min")
            model2 = build_sentence_rnn(real_vocab_number=real_vocab_number, classes=num_class,
                                        char_shape=True, word=False, char=False,
                                        cnn_encoder=cnn_encoder, highway=None, nohighway="linear",
                                        attention=False,
                                        shape_filter=shape_filter, char_filter=char_filter)
            model2.compile(loss='categorical_crossentropy',
                           optimizer='rmsprop',
                           metrics=['acc'], )
            print("START FITTING")
            model2.fit(x1_train, y_train, validation_data=(x1_val, y_val),
                       epochs=100, batch_size=BATCH_SIZE,
                       callbacks=[reducelr, stopper, checkpoint_acc, checkpoint_loss])

        print("MODEL: 4 Char Shape Only. No highway but ReLU Dense, No Attention")
        for run_num in range(MAX_RUN):
            print("RUN: ", run_num)
            checkpoint_acc = ModelCheckpoint(filepath="checkpoints/MODELRAKUTEN4_run"+str(run_num)+"_bestacc.hdf5",
                                             monitor="val_acc", verbose=1, save_best_only=True,
                                             mode="max")
            checkpoint_loss = ModelCheckpoint(filepath="checkpoints/MODELRAKUTEN4_run"+str(run_num)+"_bestloss.hdf5",
                                             monitor="val_loss", verbose=1, save_best_only=True,
                                             mode="min")
            model2 = build_sentence_rnn(real_vocab_number=real_vocab_number, classes=num_class,
                                        char_shape=True, word=False, char=False,
                                        cnn_encoder=cnn_encoder, highway=None, nohighway="relu",
                                        attention=False,
                                        shape_filter=shape_filter, char_filter=char_filter)
            model2.compile(loss='categorical_crossentropy',
                           optimizer='rmsprop',
                           metrics=['acc'], )
            print("START FITTING")
            model2.fit(x1_train, y_train, validation_data=(x1_val, y_val),
                       epochs=100, batch_size=BATCH_SIZE,
                       callbacks=[reducelr, stopper, checkpoint_acc, checkpoint_loss])

        print("MODEL: 5 Char Shape Only. Linear Highway, No Attention")
        for run_num in range(MAX_RUN):
            print("RUN: ", run_num)
            checkpoint_acc = ModelCheckpoint(filepath="checkpoints/MODELRAKUTEN5_run"+str(run_num)+"_bestacc.hdf5",
                                             monitor="val_acc", verbose=1, save_best_only=True,
                                             mode="max")
            checkpoint_loss = ModelCheckpoint(filepath="checkpoints/MODELRAKUTEN5_run"+str(run_num)+"_bestloss.hdf5",
                                             monitor="val_loss", verbose=1, save_best_only=True,
                                             mode="min")
            model2 = build_sentence_rnn(real_vocab_number=real_vocab_number, classes=num_class,
                                        char_shape=True, word=False, char=False,
                                        cnn_encoder=cnn_encoder, highway="linear", nohighway=None,
                                        attention=False,
                                        shape_filter=shape_filter, char_filter=char_filter)
            model2.compile(loss='categorical_crossentropy',
                           optimizer='rmsprop',
                           metrics=['acc'], )
            print("START FITTING")
            model2.fit(x1_train, y_train, validation_data=(x1_val, y_val),
                       epochs=100, batch_size=BATCH_SIZE,
                       callbacks=[reducelr, stopper, checkpoint_acc, checkpoint_loss])

        print("MODEL: 6 Char Shape Only. ReLU Highway, No Attention")
        for run_num in range(MAX_RUN):
            print("RUN: ", run_num)
            checkpoint_acc = ModelCheckpoint(filepath="checkpoints/MODELRAKUTEN6_run"+str(run_num)+"_bestacc.hdf5",
                                             monitor="val_acc", verbose=1, save_best_only=True,
                                             mode="max")
            checkpoint_loss = ModelCheckpoint(filepath="checkpoints/MODELRAKUTEN6_run"+str(run_num)+"_bestloss.hdf5",
                                             monitor="val_loss", verbose=1, save_best_only=True,
                                             mode="min")
            model2 = build_sentence_rnn(real_vocab_number=real_vocab_number, classes=num_class,
                                        char_shape=True, word=False, char=False,
                                        cnn_encoder=cnn_encoder, highway="relu", nohighway=None,
                                        attention=False,
                                        shape_filter=shape_filter, char_filter=char_filter)
            model2.compile(loss='categorical_crossentropy',
                           optimizer='rmsprop',
                           metrics=['acc'], )
            print("START FITTING")
            model2.fit(x1_train, y_train, validation_data=(x1_val, y_val),
                       epochs=100, batch_size=BATCH_SIZE,
                       callbacks=[reducelr, stopper, checkpoint_acc, checkpoint_loss])

    if char_only:
        print("MODEL: 7 Char Only No Highway No Attention")
        for run_num in range(MAX_RUN):
            print("RUN: ", run_num)
            checkpoint_acc = ModelCheckpoint(filepath="checkpoints/MODELRAKUTEN7_run"+str(run_num)+"_bestacc.hdf5",
                                             monitor="val_acc", verbose=1, save_best_only=True,
                                             mode="max")
            checkpoint_loss = ModelCheckpoint(filepath="checkpoints/MODELRAKUTEN7_run"+str(run_num)+"_bestloss.hdf5",
                                             monitor="val_loss", verbose=1, save_best_only=True,
                                             mode="min")
            model2 = build_sentence_rnn(real_vocab_number=real_vocab_number, char_vocab_size=char_vocab_size,
                                        classes=num_class,
                                        attention=False, word=False, char=True, char_shape=False,
                                        cnn_encoder=cnn_encoder, highway=None,
                                        shape_filter=shape_filter, char_filter=char_filter)
            model2.compile(loss='categorical_crossentropy',
                           optimizer='rmsprop',
                           metrics=['acc'], )
            model2.fit(x3_train, y_train, validation_data=(x3_val, y_val),
                       epochs=100, batch_size=BATCH_SIZE,
                       callbacks=[reducelr, stopper, checkpoint_acc, checkpoint_loss])
        print("MODEL: 8 Char Only No Highway Attention")
        for run_num in range(MAX_RUN):
            print("RUN: ", run_num)
            checkpoint_acc = ModelCheckpoint(filepath="checkpoints/MODELRAKUTEN8_run"+str(run_num)+"_bestacc.hdf5",
                                             monitor="val_acc", verbose=1, save_best_only=True,
                                             mode="max")
            checkpoint_loss = ModelCheckpoint(filepath="checkpoints/MODELRAKUTEN8_run"+str(run_num)+"_bestloss.hdf5",
                                             monitor="val_loss", verbose=1, save_best_only=True,
                                             mode="min")
            model2 = build_sentence_rnn(real_vocab_number=real_vocab_number, char_vocab_size=char_vocab_size,
                                        classes=num_class,
                                        attention=True, word=False, char=True, char_shape=False,
                                        cnn_encoder=cnn_encoder, highway=None,
                                        shape_filter=shape_filter, char_filter=char_filter)
            model2.compile(loss='categorical_crossentropy',
                           optimizer='rmsprop',
                           metrics=['acc'], )
            model2.fit(x3_train, y_train, validation_data=(x3_val, y_val),
                       epochs=100, batch_size=BATCH_SIZE,
                       callbacks=[reducelr, stopper, checkpoint_acc, checkpoint_loss])
        print("MODEL: 9 Char Only Linear Dense No Attention")
        for run_num in range(MAX_RUN):
            print("RUN: ", run_num)
            checkpoint_acc = ModelCheckpoint(filepath="checkpoints/MODELRAKUTEN9_run"+str(run_num)+"_bestacc.hdf5",
                                             monitor="val_acc", verbose=1, save_best_only=True,
                                             mode="max")
            checkpoint_loss = ModelCheckpoint(filepath="checkpoints/MODELRAKUTEN9_run"+str(run_num)+"_bestloss.hdf5",
                                             monitor="val_loss", verbose=1, save_best_only=True,
                                             mode="min")
            model2 = build_sentence_rnn(real_vocab_number=real_vocab_number, char_vocab_size=char_vocab_size,
                                        classes=num_class,
                                        attention=False, word=False, char=True, char_shape=False,
                                        cnn_encoder=cnn_encoder, highway=None, nohighway="linear",
                                        shape_filter=shape_filter, char_filter=char_filter)
            model2.compile(loss='categorical_crossentropy',
                           optimizer='rmsprop',
                           metrics=['acc'], )
            model2.fit(x3_train, y_train, validation_data=(x3_val, y_val),
                       epochs=100, batch_size=BATCH_SIZE,
                       callbacks=[reducelr, stopper, checkpoint_acc, checkpoint_loss])
        print("MODEL: 10 Char Only ReLU Dense No Attention")
        for run_num in range(MAX_RUN):
            print("RUN: ", run_num)
            checkpoint_acc = ModelCheckpoint(filepath="checkpoints/MODELRAKUTEN10_run"+str(run_num)+"_bestacc.hdf5",
                                             monitor="val_acc", verbose=1, save_best_only=True,
                                             mode="max")
            checkpoint_loss = ModelCheckpoint(filepath="checkpoints/MODELRAKUTEN10_run"+str(run_num)+"_bestloss.hdf5",
                                             monitor="val_loss", verbose=1, save_best_only=True,
                                             mode="min")
            model2 = build_sentence_rnn(real_vocab_number=real_vocab_number, char_vocab_size=char_vocab_size,
                                        classes=num_class,
                                        attention=False, word=False, char=True, char_shape=False,
                                        cnn_encoder=cnn_encoder, highway=None, nohighway="relu",
                                        shape_filter=shape_filter, char_filter=char_filter)
            model2.compile(loss='categorical_crossentropy',
                           optimizer='rmsprop',
                           metrics=['acc'], )
            model2.fit(x3_train, y_train, validation_data=(x3_val, y_val),
                       epochs=100, batch_size=BATCH_SIZE,
                       callbacks=[reducelr, stopper, checkpoint_acc, checkpoint_loss])
        print("MODEL: 11 Char Only Linear Highway No Attention")
        for run_num in range(MAX_RUN):
            print("RUN: ", run_num)
            checkpoint_acc = ModelCheckpoint(filepath="checkpoints/MODELRAKUTEN11_run"+str(run_num)+"_bestacc.hdf5",
                                             monitor="val_acc", verbose=1, save_best_only=True,
                                             mode="max")
            checkpoint_loss = ModelCheckpoint(filepath="checkpoints/MODELRAKUTEN11_run"+str(run_num)+"_bestloss.hdf5",
                                             monitor="val_loss", verbose=1, save_best_only=True,
                                             mode="min")
            model2 = build_sentence_rnn(real_vocab_number=real_vocab_number, char_vocab_size=char_vocab_size,
                                        classes=num_class,
                                        attention=False, word=False, char=True, char_shape=False,
                                        cnn_encoder=cnn_encoder, highway="linear", nohighway=None,
                                        shape_filter=shape_filter, char_filter=char_filter)
            model2.compile(loss='categorical_crossentropy',
                           optimizer='rmsprop',
                           metrics=['acc'], )
            model2.fit(x3_train, y_train, validation_data=(x3_val, y_val),
                       epochs=100, batch_size=BATCH_SIZE,
                       callbacks=[reducelr, stopper, checkpoint_acc, checkpoint_loss])
        print("MODEL: 12 Char Only ReLU Highway No Attention")
        for run_num in range(MAX_RUN):
            print("RUN: ", run_num)
            checkpoint_acc = ModelCheckpoint(filepath="checkpoints/MODELRAKUTEN12_run"+str(run_num)+"_bestacc.hdf5",
                                             monitor="val_acc", verbose=1, save_best_only=True,
                                             mode="max")
            checkpoint_loss = ModelCheckpoint(filepath="checkpoints/MODELRAKUTEN12_run"+str(run_num)+"_bestloss.hdf5",
                                             monitor="val_loss", verbose=1, save_best_only=True,
                                             mode="min")
            model2 = build_sentence_rnn(real_vocab_number=real_vocab_number, char_vocab_size=char_vocab_size,
                                        classes=num_class,
                                        attention=False, word=False, char=True, char_shape=False,
                                        cnn_encoder=cnn_encoder, highway="relu", nohighway=None,
                                        shape_filter=shape_filter, char_filter=char_filter)
            model2.compile(loss='categorical_crossentropy',
                           optimizer='rmsprop',
                           metrics=['acc'], )
            model2.fit(x3_train, y_train, validation_data=(x3_val, y_val),
                       epochs=100, batch_size=BATCH_SIZE,
                       callbacks=[reducelr, stopper, checkpoint_acc, checkpoint_loss])
    if word_only:
        print("MODEL: 13 Word Only No Attention")
        for run_num in range(MAX_RUN):
            print("RUN: ", run_num)
            checkpoint_acc = ModelCheckpoint(filepath="checkpoints/MODELRAKUTEN13_run"+str(run_num)+"_bestacc.hdf5",
                                             monitor="val_acc", verbose=1, save_best_only=True,
                                             mode="max")
            checkpoint_loss = ModelCheckpoint(filepath="checkpoints/MODELRAKUTEN13_run"+str(run_num)+"_bestloss.hdf5",
                                             monitor="val_loss", verbose=1, save_best_only=True,
                                             mode="min")
            model4 = build_sentence_rnn(real_vocab_number=real_vocab_number, word_vocab_size=word_vocab_size,
                                        classes=num_class, attention=False, char_shape=False, char=False,
                                        cnn_encoder=cnn_encoder)
            model4.compile(loss='categorical_crossentropy',
                           optimizer='rmsprop',
                           metrics=['acc'])
            model4.fit(x2_train, y_train, validation_data=(x2_val, y_val),
                       epochs=100, batch_size=BATCH_SIZE,
                       callbacks=[reducelr, stopper, checkpoint_acc, checkpoint_loss])

        print("MODEL: 14 Word Only Attention")
        for run_num in range(MAX_RUN):
            print("RUN: ", run_num)
            checkpoint_acc = ModelCheckpoint(filepath="checkpoints/MODELRAKUTEN14_run"+str(run_num)+"_bestacc.hdf5",
                                             monitor="val_acc", verbose=1, save_best_only=True,
                                             mode="max")
            checkpoint_loss = ModelCheckpoint(filepath="checkpoints/MODELRAKUTEN14_run"+str(run_num)+"_bestloss.hdf5",
                                             monitor="val_loss", verbose=1, save_best_only=True,
                                             mode="min")
            model4 = build_sentence_rnn(real_vocab_number=real_vocab_number, word_vocab_size=word_vocab_size,
                                        classes=num_class, attention=True, char_shape=False, char=False,
                                        cnn_encoder=cnn_encoder)
            model4.compile(loss='categorical_crossentropy',
                           optimizer='rmsprop',
                           metrics=['acc'])
            model4.fit(x2_train, y_train, validation_data=(x2_val, y_val),
                       epochs=100, batch_size=BATCH_SIZE,
                       callbacks=[reducelr, stopper, checkpoint_loss, checkpoint_acc])
    if hatt:
        print("MODEL: 15 HATT")
        for run_num in range(MAX_RUN):
            print("RUN: ", run_num)
            checkpoint_acc = ModelCheckpoint(filepath="checkpoints/MODELRAKUTEN15_run"+str(run_num)+"_bestacc.hdf5",
                                             monitor="val_acc", verbose=1, save_best_only=True,
                                             mode="max")
            checkpoint_loss = ModelCheckpoint(filepath="checkpoints/MODELRAKUTEN15_run"+str(run_num)+"_bestloss.hdf5",
                                             monitor="val_loss", verbose=1, save_best_only=True,
                                             mode="min")
            x_train = numpy.reshape(x2_train, (x2_train.shape[0], 5, 100))
            x_val = numpy.reshape(x2_val, (x2_val.shape[0], 5, 100))
            model = build_hatt(word_vocab_size, 2)
            model.compile(loss="categorical_crossentropy", optimizer="rmsprop", metrics=["acc"], )
            model.fit(x_train, y_train, validation_data=(x_val, y_val),
                      epochs=100, batch_size=BATCH_SIZE,
                      callbacks=[reducelr, stopper, checkpoint_acc, checkpoint_loss])

    if fasttext:
        print("MODEL: 16 fastText")
        for run_num in range(MAX_RUN):
            print("RUN: ", run_num)
            checkpoint_acc = ModelCheckpoint(filepath="checkpoints/MODELRAKUTEN16_run"+str(run_num)+"_bestacc.hdf5",
                                             monitor="val_acc", verbose=1, save_best_only=True,
                                             mode="max")
            checkpoint_loss = ModelCheckpoint(filepath="checkpoints/MODELRAKUTEN16_run"+str(run_num)+"_bestloss.hdf5",
                                             monitor="val_loss", verbose=1, save_best_only=True,
                                             mode="min")
            model = build_fasttext(word_vocab_size, 2)
            model.compile(loss="categorical_crossentropy", optimizer="rmsprop", metrics=["acc"], )
            model.fit(x2_train, y_train, validation_data=(x2_val, y_val),
                      epochs=100, batch_size=BATCH_SIZE,
                      callbacks=[reducelr, stopper, checkpoint_acc, checkpoint_loss])


def test_classifier(attention=False, cnn_encoder=True):
    x1_train_0 = numpy.random.normal(loc=4.0, scale=2.0, size=(500, MAX_SENTENCE_LENGTH, COMP_WIDTH * MAX_WORD_LENGTH))
    x1_train_1 = numpy.random.uniform(low=5, high=10, size=(500, MAX_SENTENCE_LENGTH, COMP_WIDTH * MAX_WORD_LENGTH))
    x2_train_0 = numpy.random.normal(loc=4.0, scale=2.0, size=(500, MAX_SENTENCE_LENGTH))
    x2_train_1 = numpy.random.uniform(low=5, high=10, size=(500, MAX_SENTENCE_LENGTH))
    x1_data = numpy.concatenate((x1_train_0, x1_train_1), axis=0)
    x2_data = numpy.concatenate((x2_train_0, x2_train_1), axis=0)
    labels = [0] * 500 + [1] * 500
    y_data = to_categorical(numpy.asarray(labels))

    indices = numpy.arange(x1_data.shape[0])
    numpy.random.shuffle(indices)
    data_char = x1_data[indices]
    data_word = x2_data[indices]
    y_data = y_data[indices]
    nb_validation_samples = int(VALIDATION_SPLIT * data_char.shape[0])

    x1_train = data_char[:-nb_validation_samples]
    x2_train = data_word[:-nb_validation_samples]
    y_train = y_data[:-nb_validation_samples]
    x1_val = data_char[-nb_validation_samples:]
    x2_val = data_word[-nb_validation_samples:]
    y_val = y_data[-nb_validation_samples:]

    word_vocab_size = 10

    print("Char Only")
    sgd = optimizers.SGD(lr=0.01, momentum=0.9)
    reducelr = ReduceLROnPlateau(monitor='val_loss', factor=0.1, patience=10)
    stopper = EarlyStopping(monitor='val_loss', patience=50)
    model2 = build_sentence_rnn(real_vocab_number=10, classes=2,
                                attention=attention, word=False, cnn_encoder=cnn_encoder)
    model2.compile(loss='categorical_crossentropy',
                   optimizer=sgd,
                   metrics=['acc'], )
    model2.fit(x1_train, y_train, validation_data=(x1_val, y_val),
               epochs=15, batch_size=BATCH_SIZE,
               callbacks=[reducelr, stopper])

    print("Word Only")
    sgd = optimizers.SGD(lr=0.01, momentum=0.9)
    model4 = build_sentence_rnn(real_vocab_number=10, word_vocab_size=word_vocab_size,
                                classes=2, attention=attention, char_shape=False,
                                cnn_encoder=cnn_encoder)
    model4.compile(loss='categorical_crossentropy',
                   optimizer=sgd,
                   metrics=['acc'])
    model4.fit(x2_train, y_train, validation_data=(x2_val, y_val),
               epochs=15, batch_size=BATCH_SIZE,
               callbacks=[reducelr, stopper])


def test_HATT():
    x_train_0 = numpy.random.normal(loc=4.0, scale=2.0, size=(500, MAX_SENTENCE_LENGTH))
    x_train_1 = numpy.random.uniform(low=5, high=10, size=(500, MAX_SENTENCE_LENGTH))
    x_data = numpy.concatenate((x_train_0, x_train_1), axis=0)
    labels = [0] * 500 + [1] * 500
    y_data = to_categorical(numpy.asarray(labels))
    indices = numpy.arange(x_data.shape[0])
    numpy.random.shuffle(indices)
    data_word = x_data[indices]
    y_data = y_data[indices]
    nb_validation_samples = int(VALIDATION_SPLIT * data_word.shape[0])

    x_train = data_word[:-nb_validation_samples]
    y_train = y_data[:-nb_validation_samples]
    x_val = data_word[-nb_validation_samples:]
    y_val = y_data[-nb_validation_samples:]

    word_vocab_size = 10

    x_train = numpy.reshape(x_train, (x_train.shape[0], 5, 100))
    x_val = numpy.reshape(x_val, (x_val.shape[0], 5, 100))

    model = build_hatt(word_vocab_size, 2)
    model.compile(loss="categorical_crossentropy", optimizer="rmsprop", metrics=["acc"], )
    model.fit(x_train, y_train, validation_data=(x_val, y_val), epochs=15, batch_size=BATCH_SIZE)


def test_fasttext():
    x_train_0 = numpy.random.normal(loc=4.0, scale=2.0, size=(500, MAX_SENTENCE_LENGTH))
    x_train_1 = numpy.random.uniform(low=5, high=10, size=(500, MAX_SENTENCE_LENGTH))
    x_data = numpy.concatenate((x_train_0, x_train_1), axis=0)
    labels = [0] * 500 + [1] * 500
    y_data = to_categorical(numpy.asarray(labels))
    indices = numpy.arange(x_data.shape[0])
    numpy.random.shuffle(indices)
    data_word = x_data[indices]
    y_data = y_data[indices]
    nb_validation_samples = int(VALIDATION_SPLIT * data_word.shape[0])

    x_train = data_word[:-nb_validation_samples]
    y_train = y_data[:-nb_validation_samples]
    x_val = data_word[-nb_validation_samples:]
    y_val = y_data[-nb_validation_samples:]

    word_vocab_size = 10

    model = build_fasttext(word_vocab_size, 2)
    model.compile(loss="categorical_crossentropy", optimizer="rmsprop", metrics=["acc"], )
    model.fit(x_train, y_train, validation_data=(x_val, y_val), epochs=15, batch_size=BATCH_SIZE)


if __name__ == "__main__":
    # Test Vocab
    # print(build_jp_embedding())
    #
    # for i in [4000, 5000, 8000]:
    #     print(full_vocab[i], chara_bukken_revised[i], [full_vocab[k] for k in chara_bukken_revised[i]])
    #
    # print(text_to_char_index(full_vocab=full_vocab, real_vocab_number=real_vocab_number,
    #                          chara_bukken_revised=chara_bukken_revised, mode="padding", comp_width=3))

    # from keras.models import Sequential

    # Test Word Encoder
    # model = build_word_feature()
    # model.compile('rmsprop', 'mse')
    # input_array = numpy.random.randint(5, size=(MAX_SENTENCE_LENGTH, COMP_WIDTH * MAX_WORD_LENGTH))
    # output_array = model.predict(input_array)
    # print(output_array.shape)
    # print(output_array[0])

    # Test Sentence Encoder
    # model = build_sentence_rnn(real_vocab_number=5, classes=2, attention=True, word=True, char_shape=True)
    # model.compile('rmsprop', 'mse')
    # input1_array = numpy.random.randint(5, size=(30, MAX_SENTENCE_LENGTH, COMP_WIDTH * MAX_WORD_LENGTH))
    # input2_array = numpy.random.randint(5, size=(30, MAX_SENTENCE_LENGTH, ))
    # output_array = model.predict([input1_array, input2_array])
    # print(output_array.shape)

    # Test data preprocess
    # data = kyoto_classification_job(dev_mode=False, juman=True)
    # for words in data[120:129]:
    #     for word in words:
    #         for token in word:
    #             if token != 0:
    #                 print(full_vocab[token], end="")
    #     print("\n", end="")
    # print("no attention")

    # test_classifier()
    # test_HATT()
    # test_fasttext()

    # GET THE BESTs
    print("DATASET: CH10000", flush=True)
    do_ChnSenti_classification(filename="ChnSentiCorp_htl_unba_10000/")
    print("DATASET: RAKUTEN(JP) 10000", flush=True)
    do_rakuten_senti_classification(datasize=10000)
