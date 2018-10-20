# -*- coding: utf-8 -*-
"""
Created on Wed Sep 19 06:55:24 2018

@author: nsde
"""

#%%
import torch
from torch import nn
from torch.nn import functional as F
import numpy as np
from torchvision import datasets, transforms
from torchvision.utils import make_grid
from tqdm import tqdm
import os, datetime, time
from tensorboardX import SummaryWriter
from torch_expm import torch_expm

#%%
class args:
    model = 'vae'
    batch_size = 256
    input_shape = (1, 28, 28)
    n_epochs = 500
    lr = 1e-4
    use_cuda = True
    warmup = 200
    latent_dim = 32
    only_ones = False
    n_show = 10

#%%
class Encoder(nn.Module):
    def __init__(self, input_shape, latent_dim=32, h_size=[256, 128, 64]):
        super(Encoder, self).__init__()
        self.latent_dim = latent_dim
        self.flat_dim = np.prod(input_shape)
        self.fc_layers = [nn.Linear(self.flat_dim, h_size[0])]
        for i in range(1, len(h_size)):
            self.fc_layers.append(nn.Linear(h_size[i-1], h_size[i]))
        self.fc_layers.append(nn.Linear(h_size[-1], self.latent_dim))
        self.fc_layers.append(nn.Linear(h_size[-1], self.latent_dim))
        self.activation = nn.LeakyReLU(0.1)
        self.paramlist = nn.ParameterList()
        for l in self.fc_layers:
            for p in l.parameters():
                self.paramlist.append(p)
        
    def forward(self, x):
        h = x.view(-1, self.flat_dim)
        for i in range(len(self.fc_layers)-2):
            h = self.activation(self.fc_layers[i](h))
        mu = self.fc_layers[-2](h)
        logvar = F.softplus(self.fc_layers[-1](h))
        return mu, logvar
        
#%%     
class Decoder(nn.Module):
    def __init__(self, output_shape, latent_dim=32, h_size=[64, 128, 256], 
                 end_activation=torch.sigmoid):
        super(Decoder, self).__init__()
        self.latent_dim = latent_dim
        self.output_shape = output_shape
        self.flat_dim = np.prod(output_shape)
        self.activation = nn.LeakyReLU(0.1)
        self.end_activation = end_activation
        self.fc_layers = [nn.Linear(self.latent_dim, h_size[0])]
        for i in range(1, len(h_size)):
            self.fc_layers.append(nn.Linear(h_size[i-1], h_size[i]))
        self.fc_layers.append(nn.Linear(h_size[-1], self.flat_dim))
        self.paramlist = nn.ParameterList()
        for l in self.fc_layers:
            for p in l.parameters():
                self.paramlist.append(p)
        
    def forward(self, x):
        h  = self.activation(self.fc_layers[0](x))
        for i in range(1, len(self.fc_layers)-1):
            h = self.activation(self.fc_layers[i](h))
        out = self.end_activation(self.fc_layers[-1](h))
        return out.view(-1, *self.output_shape)
            
#%%
class VAE(nn.Module):
    def __init__(self, encoder1, encoder2):
        super(VAE, self).__init__()
        self.encoder1 = encoder1
        self.encoder2 = encoder2
        
    def reparameterize(self, mu, logvar):
        if self.training:
            std = torch.exp(0.5*logvar)
            eps = torch.randn_like(std)
            return eps.mul(std).add(mu)
        else:
            return mu
        
    def forward(self, x):
        mu, logvar = self.encoder1(x)
        z = self.reparameterize(mu, logvar)
        out = self.decoder1(z)
        return out, mu, logvar
    
    def sample(self, n):
        device = next(self.parameters()).device
        with torch.no_grad():
            z = torch.randn(n, self.encoder.latent_dim, device=device)
            out = self.decoder1(z)
            return out
       
    def latent_representation(self, x):
        mu, logvar = self.encoder(x)
        z = self.reparameterize(mu, logvar)
        return z, z
   
#%%
def reconstruction_loss(recon_x, x):
    BCE = F.binary_cross_entropy(recon_x, x, reduction='sum')
    return BCE

#%%
def kullback_leibler_divergence(mu, logvar):
    KLD = -0.5 * torch.sum(1 + logvar - mu.pow(2) - logvar.exp())
    return KLD

#%%
def kl_scaling(epoch=1, warmup=1):
    return float(np.min([epoch / warmup, 1]))

#%%
if __name__ == '__main__':
    logdir = args.model + '/' + datetime.datetime.now().strftime('%Y_%m_%d_%H_%M')
    if not os.path.exists(logdir): os.makedirs(logdir)
    
    # Load data
    if args.only_ones:
        from mnist_only_ones import MNIST_only_ones
        train = MNIST_only_ones(root='', transform=transforms.ToTensor(), download=True)
        test = MNIST_only_ones(root='', train=False, transform=transforms.ToTensor(), download=True)
    else:
        train = datasets.MNIST(root='', transform=transforms.ToTensor(), download=True)
        test = datasets.MNIST(root='', train=False, transform=transforms.ToTensor(), download=True)
    trainloader = torch.utils.data.DataLoader(train, batch_size=args.batch_size)
    testloader = torch.utils.data.DataLoader(test, batch_size=args.batch_size)
    
    # Summary writer
    writer = SummaryWriter(log_dir=logdir)
    
    # Save device
    if torch.cuda.is_available() and args.use_cuda:
        device = torch.device('cuda')
    else:
        device = torch.device('cpu')
        
    # Construct model
    encoder = Encoder(input_shape=args.input_shape, latent_dim=args.latent_dim,
                      h_size=[256, 128, 64])
    decoder = Decoder(output_shape=args.input_shape, latent_dim=args.latent_dim,
                      h_size=[256, 128, 64], end_activation=torch.sigmoid)
    model = VAE(encoder, decoder)
    
    # Move model to gpu
    if torch.cuda.is_available() and args.use_cuda:
        model.cuda()
    
    # Optimizer
    optimizer = torch.optim.Adam(model.parameters(), lr=args.lr)
    
    # Main loop
    start = time.time()
    for epoch in range(1, args.n_epochs+1):
        progress_bar = tqdm(desc='Epoch ' + str(epoch), total=len(trainloader.dataset), 
                                unit='samples')
        train_loss = 0
        weight = kl_scaling(epoch, args.warmup)
        # Training loop
        model.train()
        for i, (data, _) in enumerate(trainloader):
            # Zero gradient
            optimizer.zero_grad()
            
            # Feed forward data
            data = data.reshape(-1, *args.input_shape).to(device)
            recon_data, mu, logvar = model(data)
            
            # Calculat loss
            recon_loss = reconstruction_loss(recon_data, data)
            kl1_loss = kullback_leibler_divergence(mu, logvar)
            loss = recon_loss + weight*kl1_loss
            train_loss += loss.item()
            
            # Backpropegate and optimize
            loss.backward()
            optimizer.step()
            
            # Write to consol and tensorboard
            progress_bar.update(data.size(0))
            progress_bar.set_postfix({'loss': loss.item()})
            iteration = epoch*len(trainloader) + i
            writer.add_scalar('train/total_loss', loss, iteration)
            writer.add_scalar('train/recon_loss', recon_loss, iteration)
            writer.add_scalar('train/KL_loss1', kl1_loss, iteration)
            
        progress_bar.set_postfix({'Average loss': train_loss / len(trainloader)})
        progress_bar.close()
        
        # Try out on test data
        model.eval()
        recon_loss
        for i, (data, _) in enumerate(testloader):
            data = data.reshape(-1, *args.input_shape).to(device)
            recon_data, mu, logvar = model(data)
            
            recon_loss += reconstruction_loss(recon_data, data)
            kl1_loss += kullback_leibler_divergence(mu, logvar)
        test_loss = recon_loss + weight*kl1_loss
        
        writer.add_scalar('test/total_loss', test_loss, iteration)
        writer.add_scalar('test/recon_loss', recon_loss, iteration)
        writer.add_scalar('test/KL_loss1', kl1_loss, iteration)
        
        # Save reconstructions to tensorboard
        n = args.n_show
        data_train = next(iter(trainloader))[0].to(device)[:n]
        data_test = next(iter(testloader))[0].to(device)[:n]
        recon_data_train = model(data_train)[0]
        recon_data_test = model(data_test)[0]
        
        writer.add_image('train/recon', make_grid(torch.cat([data_train, 
                         recon_data_train]).cpu(), nrow=n), global_step=epoch)
        writer.add_image('test/recon', make_grid(torch.cat([data_test, 
                         recon_data_test]).cpu(), nrow=n), global_step=epoch)
        
        # Save sample to tensorboard
        samples = model.sample(n*n)    
        writer.add_image('samples/samples', make_grid(samples.cpu(), nrow=n), 
                         global_step=epoch)
        
    print('Total train time', time.time() - start)
    
    # Save some embeddings
    print('Saving embeddings')
    all_data = torch.zeros(len(test), *args.input_shape, dtype=torch.float32, device=device)
    all_latent1 = torch.zeros(len(test), args.latent_dim, dtype=torch.float32, device=device)
    all_label = torch.zeros(len(test), dtype=torch.int32, device=device)
    counter = 0
    for i, (data, label) in enumerate(testloader):
        n = data.shape[0]
        data = data.reshape(-1, *args.input_shape).to(device)
        label = label.to(device)
        z1, _ = encoder(data)
        all_data[counter:counter+n] = data
        all_latent1[counter:counter+n] = z1
        all_label[counter:counter+n] = label
        counter += n
        
    writer.add_embedding(mat = all_latent1,
                         metadata = all_label,
                         label_img = all_data,
                         tag = 'latent_space1')
    writer.close()