from keras import backend as K
from keras import initializers, regularizers, constraints
from keras.engine import Layer


class MulEmbedding(Layer):
    """Turns positive integers (indexes) into dense vectors of fixed size.
    eg. [[4], [20]] -> [[0.25, 0.1], [0.6, -0.2]]
    This layer can only be used as the first layer in a model.
    # Example
    ```python
      model = Sequential()
      model.add(Embedding(1000, 64, input_length=10))
      # the model will take as input an integer matrix of size (batch, input_length).
      # the largest integer (i.e. word index) in the input should be no larger than 999 (vocabulary size).
      # now model.output_shape == (None, 10, 64), where None is the batch dimension.
      input_array = np.random.randint(1000, size=(32, 10))
      model.compile('rmsprop', 'mse')
      output_array = model.predict(input_array)
      assert output_array.shape == (32, 10, 64)
    ```
    # Arguments
      input_dim: int > 0. Size of the vocabulary,
          i.e. maximum integer index + 1.
      output_dim: int >= 0. Dimension of the dense embedding.
      embeddings_initializer: Initializer for the `embeddings` matrix
          (see [initializers](../initializers.md)).
      embeddings_regularizer: Regularizer function applied to
          the `embeddings` matrix
          (see [regularizer](../regularizers.md)).
      embeddings_constraint: Constraint function applied to
          the `embeddings` matrix
          (see [constraints](../constraints.md)).
      mask_zero: Whether or not the input value 0 is a special "padding"
          value that should be masked out.
          This is useful when using [recurrent layers](recurrent.md)
          which may take variable length input.
          If this is `True` then all subsequent layers
          in the model need to support masking or an exception will be raised.
          If mask_zero is set to True, as a consequence, index 0 cannot be
          used in the vocabulary (input_dim should equal size of
          vocabulary + 1).
      input_length: Length of input sequences, when it is constant.
          This argument is required if you are going to connect
          `Flatten` then `Dense` layers upstream
          (without it, the shape of the dense outputs cannot be computed).
      is_word_input: to output `(batch_size, prototypes, output_dim)`
    # Input shape
        2D tensor with shape: `(batch_size, sequence_length)`.
    # Output shape
        3D tensor with shape: `(batch_size, sequence_length, prototypes, output_dim)`.
    # References
        - [A Theoretically Grounded Application of Dropout in Recurrent Neural Networks](http://arxiv.org/abs/1512.05287)
    """

    def __init__(self, input_dim, output_prototypes, output_dim,
                 embeddings_initializer='uniform',
                 embeddings_regularizer=None,
                 activity_regularizer=None,
                 embeddings_constraint=None,
                 mask_zero=False,
                 input_length=None,
                 is_word_input=False,
                 **kwargs):
        kwargs['dtype'] = 'int32'
        if 'input_shape' not in kwargs:
            if input_length:
                kwargs['input_shape'] = (input_length,)
            else:
                kwargs['input_shape'] = (None,)
        super(MulEmbedding, self).__init__(**kwargs)

        self.input_dim = input_dim
        self.output_prototypes = output_prototypes
        self.output_dim = output_dim
        self.embeddings_initializer = initializers.get(embeddings_initializer)
        self.embeddings_regularizer = regularizers.get(embeddings_regularizer)
        self.activity_regularizer = regularizers.get(activity_regularizer)
        self.embeddings_constraint = constraints.get(embeddings_constraint)
        self.mask_zero = mask_zero
        self.input_length = input_length
        self.is_word_input = is_word_input

    def build(self, input_shape):
        self.embeddings = self.add_weight(
            shape=(self.input_dim, self.output_prototypes, self.output_dim),
            initializer=self.embeddings_initializer,
            name='embeddings',
            regularizer=self.embeddings_regularizer,
            constraint=self.embeddings_constraint)
        self.built = True

    def compute_mask(self, inputs, mask=None):
        if not self.mask_zero:
            return None
        else:
            return K.not_equal(inputs, 0)

    def compute_output_shape(self, input_shape):
        if self.input_length is None:
            if self.is_word_input:
                return input_shape[0], self.output_prototypes, self.output_dim
            else:
                return input_shape + (self.output_prototypes,) + (self.output_dim,)
        else:
            # input_length can be tuple if input is 3D or higher
            if isinstance(self.input_length, (list, tuple)):
                in_lens = list(self.input_length)
            else:
                in_lens = [self.input_length]
            if len(in_lens) != len(input_shape) - 1:
                ValueError('"input_length" is %s, but received input has shape %s' %
                           (str(self.input_length), str(input_shape)))
            else:
                for i, (s1, s2) in enumerate(zip(in_lens, input_shape[1:])):
                    if s1 is not None and s2 is not None and s1 != s2:
                        ValueError('"input_length" is %s, but received input has shape %s' %
                                   (str(self.input_length), str(input_shape)))
                    elif s1 is None:
                        in_lens[i] = s2
            return (input_shape[0],) + tuple(in_lens) + (self.output_prototypes,) + (self.output_dim,)

    def call(self, inputs):
        if K.dtype(inputs) != 'int32':
            inputs = K.cast(inputs, 'int32')
        out = K.gather(self.embeddings, inputs)
        if self.is_word_input:
            out = K.reshape(out, shape=[K.shape(out)[0], K.shape(out)[2], K.shape(out)[3]])
        return out

    def get_config(self):
        config = {'input_dim': self.input_dim,
                  'output_prototypes': self.output_prototypes,
                  'output_dim': self.output_dim,
                  'embeddings_initializer': initializers.serialize(self.embeddings_initializer),
                  'embeddings_regularizer': regularizers.serialize(self.embeddings_regularizer),
                  'activity_regularizer': regularizers.serialize(self.activity_regularizer),
                  'embeddings_constraint': constraints.serialize(self.embeddings_constraint),
                  'mask_zero': self.mask_zero,
                  'input_length': self.input_length}
        base_config = super(MulEmbedding, self).get_config()
        return dict(list(base_config.items()) + list(config.items()))


if __name__ == "__main__":
    from keras.models import Sequential
    import numpy as np

    model1 = Sequential()
    model1.add(MulEmbedding(input_dim=3, output_prototypes=4, output_dim=5, is_word_input=False))
    model1.compile('rmsprop', 'mse')
    input_array = np.random.randint(3, size=(32, 10))
    output_array = model1.predict(input_array)
    print(output_array.shape)
    assert output_array.shape == (32, 10, 4, 5)

    model2 = Sequential()
    model2.add(MulEmbedding(input_dim=3, output_prototypes=4, output_dim=5, is_word_input=True))
    model2.compile('rmsprop', 'mse')
    input_array = np.random.randint(3, size=(2,))
    output_array = model2.predict(input_array)
    print("output shape:{0}".format(output_array.shape))
    print("output type:{0}".format(output_array.__class__))
    print("output: \n{0}".format(output_array))
    assert output_array.shape == (2, 4, 5)
