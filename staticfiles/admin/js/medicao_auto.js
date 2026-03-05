document.addEventListener("DOMContentLoaded", function () {

    const contratoField = document.getElementById("id_contrato");
    if (!contratoField) return;

    contratoField.addEventListener("change", function () {

        const contratoId = this.value;
        if (!contratoId) return;

        // Remove /add/ ou /change/
        let baseUrl = window.location.pathname
            .replace("add/", "")
            .replace("change/", "");

        const url = baseUrl + "buscar-contrato/?contrato_id=" + contratoId;

        fetch(url)
            .then(response => {
                if (!response.ok) {
                    throw new Error("Erro HTTP " + response.status);
                }
                return response.json();
            })
            .then(data => {

                document.getElementById("id_fornecedor").value = data.fornecedor || "";
                document.getElementById("id_cnpj").value = data.cnpj || "";
                document.getElementById("id_responsavel").value = data.responsavel || "";
                document.getElementById("id_valor_contrato").value = data.valor_contrato || "";
                document.getElementById("id_centro_custo").value = data.centro_custo || "";

            })
            .catch(error => console.error("Erro AJAX:", error));

    });

});