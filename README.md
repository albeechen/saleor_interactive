# Saleor - More interactive 

This repository contains a legacy version of Saleor(Storefront 1.0 and Dashboard 1.0-Django views and HTML templates).

In this repository, I implemented several features:

  - Recommended system: a content-based filtering system. Find similar products in all database and set the presented priority for    customers
  - Zoom image: showing the detailed of the product image by hoving mouse around the image
  - Quick-view image: The feature changes another product image when a user browses the product list by hoving mouse on the product     image. This function saves the time to enter the detailed product page.
  - Share to Social Media: just one click that provides customers to share the product information on social media.(plugged a module  from AddToAny)
  - Wish List: similar to a shopping cart. It offers customers to save wish items on the list for future decisions.
  

<img src="https://github.com/albeec/saleor-v1/blob/master/saleor-v1/media/github-introduction/introduce_functions.png" width="85%" height="75%">

# Get Started from Saleor ver 1.0
#### Installation 
Install all dependencies:( it recommend creating a virtual environment before installing any Python packages).
```
python -m pip install -r requirements.txt
```
Set SECRET_KEY environment variable.
$env:SECRET_KEY = "<mysecretkey>"

Create a PostgreSQL user:
Use the pgAdmin tool that came with your PostgreSQL installation to create a database user for your store.
Unless configured otherwise the store will use saleor as both username and password. Remeber to give your user the SUPERUSER privilege so it can create databases and database extensions.

Prepare the database:
```
pthon manage.py migrate
```
Install front-end dependencies:
```
npm install
```
Prepare front-end assets:
```
npm run build-assets
```
Compile e-mails:
```
npm run build-emails
```
Start the development server:
```
python manage.py runserver
```
Example Data
If you’d like some data to test your new storefront you can populate the database with example products and orders:
```
python manage.py populatedb –createsuperuser
```

## License

Disclaimer: Everything you see here is open and free to use as long as you comply with the [license](https://github.com/mirumee/saleor/blob/master/LICENSE). There are no hidden charges. We promise to do our best to fix bugs and improve the code.

Some situations do call for extra code; we can cover exotic use cases or build you a custom e-commerce appliance.

#### Crafted with ❤️ by [Mirumee Software](http://mirumee.com)

hello@mirumee.com
