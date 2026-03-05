from django.contrib import admin, messages
from django.urls import path
from django.shortcuts import render, redirect
from django.db import transaction
from django.db.models import Count, Sum, Q
from django.utils.formats import number_format
from django.utils.html import format_html
from django import forms
from decimal import Decimal
import pandas as pd
import math

from mptt.admin import DraggableMPTTAdmin
from .models import PlanoContas, Compromisso, Medicao


# ==========================================
# FORM DE IMPORTAÇÃO
# ==========================================
class ImportExcelForm(forms.Form):
    arquivo = forms.FileField()


# ==========================================
# FUNÇÃO AUXILIAR DECIMAL
# ==========================================
def tratar_decimal(valor):
    if valor is None:
        return None
    if isinstance(valor, float) and math.isnan(valor):
        return None
    if str(valor).strip() == "":
        return None
    return Decimal(str(valor))


# ==========================================
# PLANO DE CONTAS ADMIN
# ==========================================
@admin.register(PlanoContas)
class PlanoContasAdmin(DraggableMPTTAdmin):

    ordering = ("tree_id", "lft")
    change_list_template = "admin/plano_contas_change_list.html"
    list_select_related = ("parent",)
    list_per_page = 1500

    list_display = (
        "tree_actions",
        "codigo_coluna",
        "descricao_coluna",
        "unidade",
        "quantidade_formatada",
        "valor_unitario_formatado",
        "valor_total_formatado",
        "valor_comprometido",
        "valor_medido",
        "saldo_a_comprometer_formatado",
        "saldo_a_medir_formatado",
    )

    list_display_links = ("codigo_coluna",)

    def eh_analitico(self, obj):
        return obj.level == 5 and not obj.get_children().exists()

    def get_queryset(self, request):

        qs = (
            super()
            .get_queryset(request)
            .select_related("parent")
            .annotate(
                filhos_count=Count("filhos"),
            )
        )

        for obj in qs:

            descendentes = PlanoContas.objects.filter(
                tree_id=obj.tree_id,
                lft__gte=obj.lft,
                rght__lte=obj.rght
            )

        obj.valor_comprometido_calc = (
            Compromisso.objects
            .filter(
                centro_custo__in=descendentes,
            )
            .aggregate(total=Sum("valor"))["total"]
            or Decimal("0.00")
        )

        obj.valor_medido_calc = (
            Medicao.objects
            .filter(
                centro_custo__in=descendentes
            )
            .aggregate(total=Sum("valor_medido"))["total"]
            or Decimal("0.00")
)

        return qs

    def codigo_coluna(self, obj):

        if not obj.is_leaf_node():
            return format_html("<b>{}</b>", obj.codigo)

        return obj.codigo

    codigo_coluna.short_description = "CÓDIGO"
    codigo_coluna.admin_order_field = "codigo"

    from django.utils.html import format_html

    def descricao_coluna(self, obj):

        if not obj.is_leaf_node():
            return format_html("<b>{}</b>", obj.descricao)

        return obj.descricao

    descricao_coluna.short_description = "DESCRIÇÃO"

    def quantidade_formatada(self, obj):
        if not self.eh_analitico(obj):
            return ""
        return number_format(obj.quantidade, 2, use_l10n=True)
    quantidade_formatada.short_description = "QTD"

    def valor_unitario_formatado(self, obj):
        if not self.eh_analitico(obj):
            return ""
        return format_html(
            '<div style="text-align:right;">{}</div>',
            number_format(obj.valor_unitario, 2, use_l10n=True)
        )
    valor_unitario_formatado.short_description = "VALOR UNIT."

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path(
                "importar-excel/",
                self.admin_site.admin_view(self.importar_excel),
                name="importar_excel",
            ),
        ]
        return custom_urls + urls

    def importar_excel(self, request):

        if request.method == "POST":
            form = ImportExcelForm(request.POST, request.FILES)

            if form.is_valid():
                arquivo = request.FILES["arquivo"]

                try:
                    df = pd.read_excel(arquivo, dtype=str)
                    df = df.where(pd.notnull(df), None)

                    # -------------------------------
                    # NORMALIZAÇÃO DOS CÓDIGOS
                    # -------------------------------
                    codigos_excel = []

                    for _, row in df.iterrows():
                        codigo = str(row.get("ITEM", "")).strip().replace(" ", "")

                        if codigo.endswith(".0"):
                            codigo = codigo[:-2]

                        if codigo:
                            codigos_excel.append(codigo)

                    # -------------------------------
                    # IDENTIFICAR QUEM POSSUI FILHOS
                    # -------------------------------
                    possui_filhos = set()

                    for codigo in codigos_excel:
                        prefixo = codigo + "."
                        for outro in codigos_excel:
                            if outro.startswith(prefixo):
                                possui_filhos.add(codigo)
                                break

                    # -------------------------------
                    # IMPORTAÇÃO
                    # -------------------------------
                    with transaction.atomic():

                        PlanoContas.objects.all().delete()

                        objetos_criados = {}

                        for _, row in df.iterrows():

                            codigo = str(row.get("ITEM", "")).strip().replace(" ", "")

                            if codigo.endswith(".0"):
                                codigo = codigo[:-2]

                            if not codigo:
                                continue

                            descricao = str(row.get("DESCRIÇÃO", "")).strip()
                            unidade = row.get("UN")
                            quantidade = tratar_decimal(row.get("QTD"))
                            valor_unitario = tratar_decimal(row.get("VALOR UNIT."))

                            partes = codigo.split(".")

                            # ------------------------------------
                            # EXPANSÃO ATÉ NÍVEL 6 (APENAS FOLHAS)
                            # ------------------------------------
                            if codigo not in possui_filhos:
                                while len(partes) < 6:
                                    partes.append("1")

                            # ------------------------------------
                            # CRIAÇÃO HIERÁRQUICA
                            # ------------------------------------
                            for i in range(1, len(partes) + 1):

                                codigo_nivel = ".".join(partes[:i])

                                if codigo_nivel in objetos_criados:
                                    continue

                                pai_codigo = ".".join(partes[:i-1]) if i > 1 else None
                                pai = objetos_criados.get(pai_codigo)

                                # níveis artificiais mantêm a descrição do último nível real
                                descricao_nivel = descricao

                                obj = PlanoContas.objects.create(
                                    codigo=codigo_nivel,
                                    descricao=descricao_nivel,
                                    parent=pai,
                                    unidade=unidade if i == len(partes) else None,
                                    quantidade=quantidade if i == len(partes) else None,
                                    valor_unitario=valor_unitario if i == len(partes) else None,
                                )

                                objetos_criados[codigo_nivel] = obj

                    self.message_user(request, "Importação concluída.", messages.SUCCESS)
                    return redirect("../")

                except Exception as e:
                    self.message_user(request, f"Erro: {e}", messages.ERROR)

        else:
            form = ImportExcelForm()

        return render(
            request,
            "admin/importar_plano_contas.html",
            {"form": form, "title": "Importar Plano de Contas"}
        )
    
    def valor_total_formatado(self, obj):
        total = obj.valor_total_consolidado
        valor = number_format(total, 2, use_l10n=True)

        if obj.filhos_count > 0:
            return format_html(
                '<div style="text-align:right;"><strong>{}</strong></div>',
                valor
            )

        return format_html(
            '<div style="text-align:right;">{}</div>',
            valor
        )
    valor_total_formatado.short_description = "VALOR TOTAL"

    def valor_comprometido(self, obj):

        valor = obj.valor_comprometido
        valor_formatado = number_format(valor, 2, use_l10n=True)

        if not obj.is_leaf_node():
            return format_html(
                '<div style="text-align:right;"><b>{}</b></div>',
                valor_formatado
            )

        return format_html(
            '<div style="text-align:right;">{}</div>',
            valor_formatado
        )

    def saldo_a_comprometer_formatado(self, obj):

        valor = obj.saldo_a_comprometer
        valor_formatado = number_format(valor, 2, use_l10n=True)

        if not obj.is_leaf_node():
            return format_html(
                '<div style="text-align:right;"><strong>{}</strong></div>',
                valor_formatado
            )

        return format_html(
            '<div style="text-align:right;">{}</div>',
            valor_formatado
        )

    saldo_a_comprometer_formatado.short_description = "SALDO A COMPROMETER"

    def valor_medido(self, obj):

        valor = obj.valor_medido or Decimal("0.00")
        valor_formatado = number_format(valor, 2, use_l10n=True)

        # verifica se possui filhos (sintético)
        if obj.get_children().exists():
            return format_html(
                '<div style="text-align:right;font-weight:bold;">{}</div>',
                valor_formatado
            )

        # analítico
        return format_html(
            '<div style="text-align:right;">{}</div>',
            valor_formatado
        )

    valor_medido.short_description = "Valor Medido"

    def saldo_a_medir_formatado(self, obj):

        valor = obj.saldo_a_medir
        valor_formatado = number_format(valor, 2, use_l10n=True)

        if not obj.is_leaf_node():
            return format_html(
                '<div style="text-align:right;"><strong>{}</strong></div>',
                valor_formatado
            )

        return format_html(
            '<div style="text-align:right;">{}</div>',
            valor_formatado
        )

    saldo_a_medir_formatado.short_description = "SALDO A MEDIR"

# ==========================================
# COMPROMISSO ADMIN
# ==========================================
@admin.register(Compromisso)
class CompromissoAdmin(admin.ModelAdmin):

    list_display = (
        "numero",
        "centro_custo_codigo",
        "cnpj",
        "fornecedor",
        "descricao",
        "tipo",
        "valor",
    )

    list_filter = (
        "tipo",
    )

    search_fields = (
        "numero",
        "centro_custo__codigo",
        "cnpj",
        "fornecedor",
        "descricao",
    )

    def centro_custo_codigo(self, obj):
        if obj.centro_custo:
            return obj.centro_custo.codigo
        return "-"

    centro_custo_codigo.short_description = "Centro de Custo"
    centro_custo_codigo.admin_order_field = "centro_custo__codigo"

# ==========================================
# MEDIÇÃO ADMIN
# ==========================================
from django.urls import path
from django.http import JsonResponse

@admin.register(Medicao)
class MedicaoAdmin(admin.ModelAdmin):

    list_display = (
        "numero_da_medicao",
        "centro_custo_codigo",
        "contrato",
        "cnpj",
        "fornecedor",
        "valor_medido",
    )

    search_fields = (
        "numero_da_medicao",
        "centro_custo__codigo",
        "centro_custo__descricao",
    )

    class Media:
        js = ("admin/js/medicao_auto.js",)

    def get_urls(self):
        urls = super().get_urls()

        custom_urls = [
            path(
                "buscar-contrato/<int:contrato_id>/",
                self.admin_site.admin_view(self.buscar_contrato),
                name="buscar_contrato",
            ),
        ]

        return custom_urls + urls

    def buscar_contrato(self, request, contrato_id):

        contrato = Compromisso.objects.get(pk=contrato_id)

        data = {
            "fornecedor": contrato.fornecedor,
            "cnpj": contrato.cnpj,
            "responsavel": contrato.responsavel,
            "valor_contrato": str(contrato.valor),
            "centro_custo": contrato.centro_custo.codigo if contrato.centro_custo else "",
        }

        return JsonResponse(data)

    def centro_custo_codigo(self, obj):
        if obj.centro_custo:
            return obj.centro_custo.codigo
        return "-"

    centro_custo_codigo.short_description = "Centro de Custo"
    centro_custo_codigo.admin_order_field = "centro_custo__codigo"