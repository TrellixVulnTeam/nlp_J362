from typing import Dict, Optional

import numpy
from overrides import overrides
import torch
import torch.nn.functional as F

from allennlp.data import Vocabulary
from allennlp.modules import FeedForward, Seq2SeqEncoder, Seq2VecEncoder, TextFieldEmbedder
from allennlp.models.model import Model
from allennlp.nn import InitializerApplicator, RegularizerApplicator
from allennlp.nn import util
from allennlp.training.metrics import CategoricalAccuracy, F1Measure

from hznlp.models.matching_layer import MatchingLayer


@Model.register("bimpm")
class BiMPM(Model):
    def __init__(self, vocab: Vocabulary,
                 text_field_embedder: TextFieldEmbedder,
                 encoder: Seq2SeqEncoder,
                 matcher: MatchingLayer,
                 aggregator: Seq2VecEncoder,
                 classifier_feedforward: FeedForward,
                 dropout: float = 0.1,
                 initializer: InitializerApplicator = InitializerApplicator(),
                 regularizer: Optional[RegularizerApplicator] = None) -> None:
        super(BiMPM, self).__init__(vocab, regularizer)

        self.text_field_embedder = text_field_embedder
        self.num_classes = self.vocab.get_vocab_size("label")
        self.encoder = encoder
        self.matcher = matcher
        self.aggregator = aggregator
        self.classifier_feedforward = classifier_feedforward

        self.dropout = torch.nn.Dropout(dropout)

        self.metrics = {
            "accuracy": CategoricalAccuracy()
        }

        self.loss = torch.nn.CrossEntropyLoss()

        initializer(self)

    @overrides
    def forward(self,  # type: ignore
                premise: Dict[str, torch.LongTensor],
                hypothesis: Dict[str, torch.LongTensor],
                label: torch.LongTensor = None) -> Dict[str, torch.Tensor]:

        mask_p = util.get_text_field_mask(premise)
        mask_h = util.get_text_field_mask(hypothesis)

        embedded_p = self.dropout(self.text_field_embedder(premise))
        encoded_p = self.dropout(self.encoder(embedded_p, mask_p))

        embedded_h = self.dropout(self.text_field_embedder(hypothesis))
        encoded_h = self.dropout(self.encoder(embedded_h, mask_h))

        mv_p, mv_h = self.matcher(encoded_p, mask_p, encoded_h, mask_h)
        mv_p, mv_h = self.dropout(mv_p), self.dropout(mv_h)

        agg_p = self.dropout(self.aggregator(mv_p, mask_p))
        agg_h = self.dropout(self.aggregator(mv_h, mask_h))

        logits = self.classifier_feedforward(torch.cat([agg_p, agg_h], dim=-1))

        output_dict = {'logits': logits}
        if label is not None:
            loss = self.loss(logits, label.squeeze(-1))
            for metric in self.metrics.values():
                metric(logits, label.squeeze(-1))
            output_dict["loss"] = loss

        return output_dict

    @overrides
    def decode(self, output_dict: Dict[str, torch.Tensor]) -> Dict[str, torch.Tensor]:
        """
        Does a simple argmax over the class probabilities, converts indices to string labels, and
        adds a ``"label"`` key to the dictionary with the result.
        """
        class_probabilities = F.softmax(output_dict['logits'], dim=-1)
        output_dict['class_probabilities'] = class_probabilities

        predictions = class_probabilities.cpu().data.numpy()
        argmax_indices = numpy.argmax(predictions, axis=-1)
        labels = [self.vocab.get_token_from_index(x, namespace="labels")
                  for x in argmax_indices]
        output_dict['label'] = labels
        return output_dict

    @overrides
    def get_metrics(self, reset: bool = False) -> Dict[str, float]:
        return {metric_name: metric.get_metric(reset) for metric_name, metric in self.metrics.items()}

