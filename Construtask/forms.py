from django import forms

class ImportarPlanoContasForm(forms.Form):
    arquivo = forms.FileField()
