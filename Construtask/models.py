from django.db import models
from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.db.models import Sum, Q
from decimal import Decimal, ROUND_HALF_UP
from mptt.models import MPTTModel, TreeForeignKey
from mptt.managers import TreeManager
import re

# =====================================================
# PLANO DE CONTAS
# =====================================================

class PlanoContas(MPTTModel):

    codigo = models.CharField(max_length=50, unique=True, editable=False)
    descricao = models.CharField(max_length=255)

    parent = TreeForeignKey(
        "self",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="filhos"
    )

    unidade = models.CharField(max_length=20, null=True, blank=True)
    quantidade = models.DecimalField(max_digits=15, decimal_places=2, null=True, blank=True)
    valor_unitario = models.DecimalField(max_digits=15, decimal_places=2, null=True, blank=True)

    valor_total = models.DecimalField(
        max_digits=18,
        decimal_places=2,
        default=Decimal("0.00")
    )

    objects = TreeManager()

    class MPTTMeta:
        order_insertion_by = ["codigo"]

    class Meta:
        indexes = [
            models.Index(fields=["codigo"]),
        ]

    def __str__(self):
        return f"{self.codigo} - {self.descricao}"

    # =====================================================
    # GERAÇÃO DE CÓDIGO
    # =====================================================

    def gerar_codigo(self):
        def formatar(numero):
            return str(numero).zfill(2)

        if self.pai:
            filhos = self.pai.get_children().order_by("-codigo")
            if filhos.exists():
                ultimo_codigo = filhos.first().codigo
                partes = ultimo_codigo.split(".")
                ultimo_numero = int(partes[-1])
                partes[-1] = formatar(ultimo_numero + 1)
                return ".".join(partes)
            return f"{self.pai.codigo}.01"

        raizes = PlanoContas.objects.filter(pai__isnull=True).order_by("-codigo")
        if raizes.exists():
            ultimo_codigo = raizes.first().codigo
            return formatar(int(ultimo_codigo) + 1)

        return "01"

    # =====================================================
    # VALIDAÇÃO
    # =====================================================

    def clean(self):
        nivel = self.level if self.pk else (
            self.pai.level + 1 if self.pai else 0
        )

        if nivel < 5:
            if self.unidade or self.quantidade or self.valor_unitario:
                raise ValidationError(
                    "Unidade, quantidade e valor unitário só podem existir no nível 6."
                )

    # =====================================================
    # SAVE
    # =====================================================

    def save(self, *args, **kwargs):

        if not self.codigo:
            self.codigo = self.gerar_codigo()

        # cálculo automático do valor total
        if self.quantidade is not None and self.valor_unitario is not None:
            self.valor_total = (
                Decimal(self.quantidade) * Decimal(self.valor_unitario)
            ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        super().save(*args, **kwargs)

    # =====================================================
    # PROPRIEDADES OTIMIZADAS (SEM RECURSÃO)
    # =====================================================

    @property
    def valor_total_consolidado(self):
        return (
            self.get_descendants(include_self=True)
            .aggregate(total=Sum("valor_total"))["total"]
            or Decimal("0.00")
        )

    @property
    def valor_comprometido(self):
        return (
            self.get_descendants(include_self=True)
            .aggregate(
                total=Sum(
                    "compromissos__valor",
                )
            )["total"]
            or Decimal("0.00")
        )

    @property
    def valor_medido(self):
        return (
            self.get_descendants(include_self=True)
            .aggregate(
                total=Sum("medicoes__valor_medido")
            )["total"]
            or Decimal("0.00")
        )

    @property
    def saldo_a_comprometer(self):
        valor = self.valor_total_consolidado - self.valor_comprometido
        return valor.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    @property
    def saldo_a_medir(self):
        valor = self.valor_comprometido - self.valor_medido
        return valor.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


# =====================================================
# COMPROMISSOS
# =====================================================

cnpj_validator = RegexValidator(
    regex=r'^\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}$',
    message="CNPJ deve estar no formato XX.XXX.XXX/XXXX-XX"
)


class Compromisso(models.Model):

    TIPO_CHOICES = (
        ("CONTRATO", "Contrato (Serviço)"),
        ("PEDIDO_COMPRA", "Pedido de Compra (Material)"),
    )

    numero = models.CharField(max_length=30, unique=True)
    tipo = models.CharField(max_length=20, choices=TIPO_CHOICES)

    centro_custo = models.ForeignKey(
        PlanoContas,
        on_delete=models.PROTECT,
        related_name="compromissos",
        null=True,
        blank=True
    )

    descricao = models.CharField(max_length=500)
    fornecedor = models.CharField(max_length=150)

    cnpj = models.CharField(max_length=18, validators=[cnpj_validator])

    responsavel = models.CharField(max_length=150)
    telefone = models.CharField(max_length=20)

    valor = models.DecimalField(max_digits=15, decimal_places=2)
    data_assinatura = models.DateField()
    criado_em = models.DateTimeField(auto_now_add=True)

    # =====================================================
    # VALIDAÇÃO ORÇAMENTÁRIA
    # =====================================================

    def clean(self):
        super().clean()

        if not self.centro_custo:
            return

        orcamento = self.centro_custo.valor_total_consolidado

        total_compromissos = (
            Compromisso.objects
            .filter(centro_custo=self.centro_custo)
            .exclude(pk=self.pk)
            .aggregate(total=Sum("valor"))["total"]
            or Decimal("0.00")
        )

        saldo_disponivel = orcamento - total_compromissos

        if self.valor > saldo_disponivel:
            raise ValidationError(
                f"Valor excede orçamento disponível.\n"
                f"Saldo disponível: {saldo_disponivel}"
            )

    # =====================================================
    # SAVE
    # =====================================================

    def save(self, *args, **kwargs):

        prefixo = "CTR-" if self.tipo == "CONTRATO" else "PED-"

        if not self.numero or not self.numero.startswith(prefixo):

            ultimo = (
                Compromisso.objects
                .filter(numero__startswith=prefixo)
                .order_by("-id")
                .first()
            )

            novo_num = int(ultimo.numero.replace(prefixo, "")) + 1 if ultimo else 1
            self.numero = f"{prefixo}{str(novo_num).zfill(4)}"

        super().save(*args, **kwargs)

    def __str__(self):
        return self.numero


# =====================================================
# MEDIÇÕES
# =====================================================

class Medicao(models.Model):

    contrato = models.ForeignKey(
        Compromisso,
        on_delete=models.PROTECT,
        related_name="medicoes"
    )

    fornecedor = models.CharField(max_length=150, blank=True)
    cnpj = models.CharField(max_length=18, blank=True)
    responsavel = models.CharField(max_length=150, blank=True)

    valor_contrato = models.DecimalField(max_digits=15, decimal_places=2, null=True, blank=True)

    centro_custo = models.ForeignKey(
        PlanoContas,
        on_delete=models.PROTECT,
        related_name="medicoes",
        null=True,
        blank=True
    )

    numero_da_medicao = models.CharField(max_length=30, unique=True, null=True, blank=True)
    data_medicao = models.DateField()

    descricao = models.CharField(max_length=900, blank=False)

    valor_medido = models.DecimalField(max_digits=15, decimal_places=2)

    @property
    def valor_total_consolidado(self):

        if self.is_leaf_node():
            qtd = self.quantidade or Decimal("0")
            unit = self.valor_unitario or Decimal("0")
            return qtd * unit

        total = Decimal("0")
        for filho in self.get_children():
            total += filho.valor_total_consolidado

        return total
    
    criado_em = models.DateTimeField(auto_now_add=True)


    # =====================================================
    # VALIDAÇÃO
    # =====================================================

    def clean(self):
        super().clean()

        if self.contrato.tipo != "CONTRATO":
            raise ValidationError("Medições só podem ser vinculadas a contratos.")

        total_medido = (
            self.contrato.medicoes
            .exclude(pk=self.pk)
            .aggregate(total=Sum("valor_medido"))["total"]
            or Decimal("0.00")
        )

        saldo_atual = self.contrato.valor - total_medido

        if self.valor_medido > saldo_atual:
            raise ValidationError("Valor medido excede saldo do contrato.")

    # =====================================================
    # SAVE
    # =====================================================

    def save(self, *args, **kwargs):

        contrato = self.contrato

        self.fornecedor = contrato.fornecedor
        self.cnpj = contrato.cnpj
        self.responsavel = contrato.responsavel
        self.valor_contrato = contrato.valor
        self.centro_custo = contrato.centro_custo

        prefixo = "MED-"

        if not self.numero_da_medicao or not self.numero_da_medicao.startswith(prefixo):

            ultimo = (
                Medicao.objects
                .filter(numero_da_medicao__startswith=prefixo)
                .order_by("-id")
                .first()
            )

            novo_num = int(ultimo.numero_da_medicao.replace(prefixo, "")) + 1 if ultimo else 1
            self.numero_da_medicao = f"{prefixo}{str(novo_num).zfill(4)}"

        super().save(*args, **kwargs)