import lasagne, theano, numpy as np, logging
from theano import tensor as T

class Identity(lasagne.init.Initializer):

    def sample(self, shape):
        return lasagne.utils.floatX(np.eye(*shape))

class RDNN:
    def __init__(self, nc, nf, max_seq_length, **kwargs):
        # batch_size=None, n_hidden=100, grad_clip=7, lr=.001):
        assert nf; assert max_seq_length
        LayerType = lasagne.layers.RecurrentLayer
        if kwargs['ltype'] == 'recurrent':
            LayerType = lasagne.layers.RecurrentLayer
        elif kwargs['ltype'] == 'lstm':
            LayerType = lasagne.layers.LSTMLayer
        else:
            raise Exception()
        nonlin = getattr(lasagne.nonlinearities, kwargs['activation'])
        optim = getattr(lasagne.updates, kwargs['opt'])
        n_hidden = kwargs['n_hidden']
        grad_clip = kwargs['grad_clip']
        lr = kwargs['lr']
        batch_size = kwargs['n_batch'] # TODO: not used
        ldepth = len(kwargs['n_hidden'])

        # network
        l_in = lasagne.layers.InputLayer(shape=(None, max_seq_length, nf))
        logging.info('l_in: {}'.format(lasagne.layers.get_output_shape(l_in)))
        N_BATCH_VAR, _, _ = l_in.input_var.shape # symbolic ref to input_var shape
        l_mask = lasagne.layers.InputLayer(shape=(N_BATCH_VAR, max_seq_length))
        logging.info('l_mask: {}'.format(lasagne.layers.get_output_shape(l_mask)))

        self.layers = [l_in]
        for level in range(1,ldepth+1):
            prev_layer = self.layers[level-1]
            l_forward = LayerType(prev_layer, n_hidden[level-1], mask_input=l_mask, grad_clipping=grad_clip, W_hid_to_hid=Identity(), b=lasagne.init.Constant(0.001),  nonlinearity=nonlin)
            logging.info('l_forward: {}'.format(lasagne.layers.get_output_shape(l_forward)))
            l_backward = LayerType(prev_layer, n_hidden[level-1], mask_input=l_mask, grad_clipping=grad_clip, W_hid_to_hid=Identity(), b=lasagne.init.Constant(0.001), nonlinearity=nonlin, backwards=True)
            logging.info('l_backward: {}'.format(lasagne.layers.get_output_shape(l_backward)))
            """
            l_sum = lasagne.layers.ElemwiseSumLayer([l_forward, l_backward])
            print 'l_sum:', lasagne.layers.get_output_shape(l_sum)
            self.layers.append(l_sum)
            """
            l_concat = lasagne.layers.ConcatLayer([l_forward, l_backward], axis=2)
            logging.info('l_concat: {}'.format(lasagne.layers.get_output_shape(l_concat)))
            self.layers.append(l_concat)

        l_reshape = lasagne.layers.ReshapeLayer(self.layers[-1], (-1, n_hidden[-1]*2))
        logging.info('l_reshape: {}'.format(lasagne.layers.get_output_shape(l_reshape)))
        l_rec_out = lasagne.layers.DenseLayer(l_reshape, num_units=nc, nonlinearity=lasagne.nonlinearities.softmax)
        logging.info('l_rec_out: {}'.format(lasagne.layers.get_output_shape(l_rec_out)))
        l_out = lasagne.layers.ReshapeLayer(l_rec_out, (N_BATCH_VAR, max_seq_length, nc))
        logging.info('l_out: {}'.format(lasagne.layers.get_output_shape(l_out)))

        self.output_layer = l_out

        target_output = T.tensor3('target_output')
        out_mask = T.tensor3('mask')

        def cost(output):
             return -T.sum(out_mask*target_output*T.log(output))/T.sum(out_mask)

        cost_train = cost(lasagne.layers.get_output(l_out, deterministic=False))
        cost_eval = cost(lasagne.layers.get_output(l_out, deterministic=True))

        cost_train = T.switch(T.or_(T.isnan(cost_train), T.isinf(cost_train)), 1000, cost_train)


        all_params = lasagne.layers.get_all_params(l_out, trainable=True)

        f_hid2hid = l_forward.get_params()[-1]
        b_hid2hid = l_backward.get_params()[-1]

        grads = T.grad(cost_train, all_params)

        # Compute SGD updates for training
        logging.info("Computing updates ...")
        if kwargs['opt'] == 'adam':
            # updates = lasagne.updates.adam(grads, all_params, lr, beta1=0.1, beta2=0.001) # TODO
            updates = lasagne.updates.momentum(grads, all_params, lr)
        else:
            updates = optim(cost_train, all_params, lr)
        # Theano functions for training and computing cost
        logging.info("Compiling functions ...")
        self.train_model = theano.function(
                inputs=[l_in.input_var, target_output, l_mask.input_var, out_mask],
                outputs=[cost_train, lasagne.layers.get_output(l_out, deterministic=True), lasagne.layers.get_output(l_concat, deterministic=True), f_hid2hid, b_hid2hid], updates=updates)
        self.compute_cost = theano.function([l_in.input_var, target_output, l_mask.input_var, out_mask], cost_eval)
        self.compute_cost_train = theano.function([l_in.input_var, target_output, l_mask.input_var, out_mask], cost_train)
        self.predict_model = theano.function(
                # inputs=[l_in.input_var, l_mask.input_var],
                inputs=[l_in.input_var, target_output, l_mask.input_var, out_mask],
                outputs=[cost_eval, lasagne.layers.get_output(l_out, deterministic=True)])

    def sing(self, dsetdat, mode):
        ecost, rnn_last_predictions = 0, []
        for Xdset, Xdsetmsk, ydset, ydsetmsk in zip(*dsetdat):
            if mode == 'train':
                bcost, pred, l_sum_out, f_hid2hid, b_hid2hid = self.train_model(Xdset, ydset, Xdsetmsk, ydsetmsk)
                logging.debug('cost: {} mean {} max {} min {}'.format(bcost, np.mean(l_sum_out), np.max(l_sum_out), np.min(l_sum_out)))
                logging.debug('forward mean {} max {} min {}'.format(np.mean(f_hid2hid), np.max(f_hid2hid), np.min(f_hid2hid)))
                logging.debug('backwar mean {} max {} min {}'.format(np.mean(b_hid2hid), np.max(b_hid2hid), np.min(b_hid2hid)))
                # print 'mean {} max {} min {}'.format(np.mean(grads), np.max(grads), np.min(grads))
            else:
                bcost, pred = getattr(self, mode+'_model')(Xdset, ydset, Xdsetmsk, ydsetmsk)
            ecost += bcost
            predictions = np.argmax(pred*ydsetmsk, axis=-1).flatten()
            sentLens, mlen = Xdsetmsk.sum(axis=-1), Xdset.shape[1]
            for i, slen in enumerate(sentLens):
                rnn_last_predictions.append(predictions[i*mlen:i*mlen+slen])
        return ecost, rnn_last_predictions
