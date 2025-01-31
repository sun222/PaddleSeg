import numpy as np
import os
import random
from typing import List, Tuple

import paddle as torch
import paddle
from paddle import Tensor, nn

def compute_mhsa(q, k, v, scale_factor=1, mask=None):
    # resulted shape will be: [batch, heads, tokens, tokens]

    k=paddle.transpose(k,perm=[0,1,3,2])
    scaled_dot_prod = paddle.matmul(q,k) * scale_factor


    # scaled_dot_prod = torch.einsum('... i d , ... j d -> ... i j', q, k) * scale_factor

    if mask is not None:
        assert mask.shape == scaled_dot_prod.shape[2:]
        scaled_dot_prod = scaled_dot_prod.masked_fill(mask, -np.inf)

    attention = torch.nn.functional.softmax(scaled_dot_prod, axis=-1)
    # calc result per head

    return paddle.matmul(attention,v)  
    # return torch.einsum('... i j , ... j d -> ... i d', attention, v)


class MultiHeadSelfAttention(nn.Layer):
    def __init__(self, dim, heads=8, dim_head=None):
        """
        Implementation of multi-head attention layer of the original transformer model.
        einsum and einops.rearrange is used whenever possible
        Args:
            dim: token's dimension, i.e. word embedding vector size
            heads: the number of distinct representations to learn
            dim_head: the dim of the head. In general dim_head<dim.
            However, it may not necessary be (dim/heads)
        """
        super().__init__()
        self.dim_head = (int(dim / heads)) if dim_head is None else dim_head
        _dim = self.dim_head * heads
        self.heads = heads
        self.to_qvk = nn.Linear(dim, _dim * 3, bias_attr=False)
        self.W_0 = nn.Linear(_dim, dim, bias_attr=False)
        self.scale_factor = self.dim_head ** -0.5

    def forward(self, x, mask=None):

        # 1 64 768
        assert x.dim() == 3
        qkv = self.to_qvk(x)  # [batch, tokens, dim*3*heads ]

        # 1 64 2304      
        # decomposition to q,v,k and cast to tuple
        # the resulted shape before casting to tuple will be: [3, batch, heads, tokens, dim_head]


        shape =qkv.shape
        k=3
        h=self.heads

        d=paddle.reshape(qkv,[shape[0],shape[1],-1,k,h])
        d=paddle.transpose(d,perm=[3,0,4,1,2])

        
        q, k, v = tuple(d)
        # 1 12 64 64

        # q, k, v = tuple(rearrange(qkv, 'b t (d k h ) -> k b h t d ', k=3, h=self.heads))

        out = compute_mhsa(q, k, v, mask=mask, scale_factor=self.scale_factor)
  
        #  [1, 12, 64, 64]
        # re-compose: merge heads with dim_head

      
        d=paddle.transpose(out,perm=[0,2,1,3])
        shape =d.shape 

        d=paddle.reshape(d,[shape[0],shape[1],-1])
        out=d
        # out = rearrange(out, "b h t d -> b t (h d)")
        # Apply final linear transformation layer

        #1 64 12*64
        return self.W_0(out)





# from https://huggingface.co/transformers/_modules/transformers/modeling_utils.html
def get_module_device(parameter: nn.layer):
    try:
        return next(parameter.parameters()).device
    except StopIteration:
        # For nn.DataParallel compatibility in PyTorch 1.5

        def find_tensor_attributes(module: nn.Layer) -> List[Tuple[str, Tensor]]:
            tuples = [(k, v) for k, v in module.__dict__.items() if torch.is_tensor(v)]
            return tuples

        gen = parameter._named_members(get_members_fn=find_tensor_attributes)
        first_tuple = next(gen)
        return first_tuple[1].device



class TransformerBlock(nn.Layer):
    """
    Vanilla transformer block from the original paper "Attention is all you need"
    Detailed analysis: https://theaisummer.com/transformer/
    """

    def __init__(self, dim, heads=8, dim_head=None,
                 dim_linear_block=1024, dropout=0.1, activation=nn.GELU,
                 mhsa=None, prenorm=False):
        """
        Args:
            dim: token's vector length
            heads: number of heads
            dim_head: if none dim/heads is used
            dim_linear_block: the inner projection dim
            dropout: probability of droppping values
            mhsa: if provided you can change the vanilla self-attention block
            prenorm: if the layer norm will be applied before the mhsa or after
        """
        super().__init__()
        self.mhsa = mhsa if mhsa is not None else MultiHeadSelfAttention(dim=dim, heads=heads, dim_head=dim_head)
        self.prenorm = prenorm
        self.drop = nn.Dropout(dropout)
        self.norm_1 = nn.LayerNorm(dim)
        self.norm_2 = nn.LayerNorm(dim)

        self.linear = nn.Sequential(
            nn.Linear(dim, dim_linear_block),
            activation(),  # nn.ReLU or nn.GELU
            nn.Dropout(dropout),
            nn.Linear(dim_linear_block, dim),
            nn.Dropout(dropout)
        )

    def forward(self, x, mask=None):
        if self.prenorm:
            y = self.drop(self.mhsa(self.norm_1(x), mask)) + x
            out = self.linear(self.norm_2(y)) + y
        else:
            y = self.norm_1(self.drop(self.mhsa(x, mask)) + x)
            out = self.norm_2(self.linear(y) + y)
        return out


class TransformerEncoder(nn.Layer):
    def __init__(self, dim, blocks=6, heads=8, dim_head=None, dim_linear_block=1024, dropout=0, prenorm=False):
        super().__init__()
        self.block_list = [TransformerBlock(dim, heads, dim_head,
                                            dim_linear_block, dropout, prenorm=prenorm) for _ in range(blocks)]
        self.layers = nn.LayerList(self.block_list)

    def forward(self, x, mask=None):
        for layer in self.layers:
            x = layer(x, mask)
        return x